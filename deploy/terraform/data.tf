# --- Postgres ----------------------------------------------------------------

resource "random_password" "db" {
  length  = 32
  special = false # avoids URL-encoding problems in the connection string
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "Postgres, reachable only from the EKS nodes"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Postgres from cluster nodes"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  # No egress rules at all. The database has no reason to originate traffic,
  # and this subnet has no internet route regardless.

  tags = { Name = "${local.name}-db" }
}

resource "aws_db_instance" "main" {
  identifier = "${local.name}-postgres"

  # pgvector ships as an available extension from Postgres 15.2 onward; the
  # migration issues CREATE EXTENSION vector. On an older engine that migration
  # fails at deploy time rather than silently degrading, which is the right
  # way round.
  engine         = "postgres"
  engine_version = "16.4"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 5 # autoscale rather than page at 3am
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "safeshield"
  username = "safeshield"
  password = random_password.db.result
  port     = 5432

  db_subnet_group_name   = module.vpc.database_subnet_group_name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false

  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "Mon:04:00-Mon:05:00"

  # Single-AZ to keep the demo affordable. This is the difference between a
  # failover measured in seconds and a restore measured in hours, so it is a
  # cost decision rather than a default worth copying.
  multi_az = false

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = !var.deletion_protection
  final_snapshot_identifier = var.deletion_protection ? "${local.name}-final" : null

  performance_insights_enabled    = true
  enabled_cloudwatch_logs_exports = ["postgresql"]

  # Applied in the maintenance window, not mid-request.
  apply_immediately = false
}

# --- Redis -------------------------------------------------------------------

resource "aws_security_group" "redis" {
  name        = "${local.name}-redis"
  description = "Redis, reachable only from the EKS nodes"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Redis from cluster nodes"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  tags = { Name = "${local.name}-redis" }
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${local.name}-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${local.name}-redis"
  description          = "Job queue and rate limiting"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.redis_node_type
  port           = 6379

  num_cache_clusters         = 1
  automatic_failover_enabled = false

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  # In-transit encryption is deliberately off: enabling it requires every
  # client to speak TLS, and arq's connection settings would need reworking.
  # The traffic never leaves the VPC's private subnets. Revisit if that
  # changes.
  transit_encryption_enabled = false

  # The queue is not the system of record -- documents live in Postgres and S3,
  # and the requeue sweep re-drives anything lost. Snapshots would cost money
  # to protect data that is reconstructible.
  snapshot_retention_limit = 0

  maintenance_window = "tue:04:00-tue:05:00"
}
