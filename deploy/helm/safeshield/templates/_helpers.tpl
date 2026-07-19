{{/* Standard name helpers. */}}

{{- define "safeshield.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "safeshield.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "safeshield.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "safeshield.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "safeshield.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "safeshield.selectorLabels" -}}
app.kubernetes.io/name: {{ include "safeshield.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "safeshield.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "safeshield.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
The image tag. Deliberately fails rather than defaulting to `latest`: a moving
tag makes a rollout unreproducible and a rollback meaningless, because the tag
has already moved to the thing you are rolling back from.
*/}}
{{- define "safeshield.image" -}}
{{- $tag := .Values.image.tag | default "" }}
{{- if not $tag }}
{{- fail "image.tag must be set to an immutable tag (a commit sha). Refusing to default to `latest`." }}
{{- end }}
{{- printf "%s:%s" .Values.image.repository $tag }}
{{- end }}

{{/*
Environment shared by the API, the worker and the migration job. Defined once
so the three cannot drift -- a worker configured against a different database
than the API is the kind of thing that only shows up as missing data.
*/}}
{{- define "safeshield.env" -}}
- name: ENV
  value: {{ .Values.config.env | quote }}
- name: JSON_LOGS
  value: {{ .Values.config.jsonLogs | quote }}
- name: DEBUG
  value: "false"
- name: COOKIE_SECURE
  value: "true"
- name: CORS_ORIGINS
  value: {{ .Values.config.corsOrigins | toJson | quote }}
- name: STORAGE_BACKEND
  value: {{ .Values.config.storage.backend | quote }}
{{- if eq .Values.config.storage.backend "s3" }}
- name: S3_BUCKET
  value: {{ required "config.storage.bucket must be set when the storage backend is s3" .Values.config.storage.bucket | quote }}
- name: S3_PREFIX
  value: {{ .Values.config.storage.prefix | quote }}
{{- end }}
- name: RETRIEVAL_TOP_K
  value: {{ .Values.config.retrieval.topK | quote }}
- name: RETRIEVAL_CANDIDATES
  value: {{ .Values.config.retrieval.candidates | quote }}
- name: EMBEDDING_MODEL
  value: {{ .Values.config.openai.embeddingModel | quote }}
- name: CHAT_MODEL
  value: {{ .Values.config.openai.chatModel | quote }}
- name: OPENAI_TIMEOUT_SECONDS
  value: {{ .Values.config.openai.timeoutSeconds | quote }}
{{- range $field, $key := .Values.existingSecret.keys }}
- name: {{ $key }}
  valueFrom:
    secretKeyRef:
      name: {{ $.Values.existingSecret.name }}
      key: {{ $key }}
{{- end }}
{{- end }}

{{/*
Pod-level security context. Applied to every workload: the container has no
business writing to its own filesystem or running as root, and saying so is
what makes an exploited dependency less useful to whoever exploited it.
*/}}
{{- define "safeshield.securityContext" -}}
runAsNonRoot: true
runAsUser: 1000
runAsGroup: 1000
fsGroup: 1000
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "safeshield.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop:
    - ALL
{{- end }}
