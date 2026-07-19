module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.31"

  cluster_name    = local.name
  cluster_version = var.kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # Public endpoint so kubectl works from a laptop without a bastion. For
  # anything real, restrict cluster_endpoint_public_access_cidrs to known
  # addresses -- an open API server endpoint is a large attack surface even
  # with authentication in front of it.
  cluster_endpoint_public_access  = true
  cluster_endpoint_private_access = true

  # The modern access path. aws-auth ConfigMap editing was the old way and is
  # famously easy to lock yourself out of.
  authentication_mode                      = "API_AND_CONFIG_MAP"
  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns    = { most_recent = true }
    kube-proxy = { most_recent = true }
    vpc-cni = {
      most_recent = true
      # Needed for NetworkPolicy to be enforced at all. Without it the policies
      # in the Helm chart are accepted by the API server and quietly ignored,
      # which is worse than not having them.
      configuration_values = jsonencode({
        enableNetworkPolicy = "true"
      })
    }
    eks-pod-identity-agent = { most_recent = true }
  }

  eks_managed_node_groups = {
    default = {
      instance_types = var.node_instance_types
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      desired_size   = var.node_min_size

      # Nodes hold the extraction workload, which loads a whole PDF and its
      # parsed blocks into memory. 20GB leaves room for images and ephemeral
      # storage without paying for space nothing uses.
      disk_size = 20

      labels = {
        workload = "safeshield"
      }
    }
  }

  node_security_group_additional_rules = {
    # The metrics server and Prometheus Operator both need the control plane
    # to reach webhook and metrics ports on the nodes.
    ingress_cluster_to_node_all = {
      description                   = "Control plane to node"
      protocol                      = "-1"
      from_port                     = 0
      to_port                       = 0
      type                          = "ingress"
      source_cluster_security_group = true
    }
  }
}
