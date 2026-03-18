{{/*
Expand the name of the chart.
*/}}
{{- define "asibot.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a fully qualified app name.
*/}}
{{- define "asibot.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "asibot.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "asibot.labels" -}}
helm.sh/chart: {{ include "asibot.chart" . }}
{{ include "asibot.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "asibot.selectorLabels" -}}
app.kubernetes.io/name: {{ include "asibot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Container image reference.
*/}}
{{- define "asibot.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository $tag }}
{{- end }}

{{/*
Secret name (supports existing secret).
*/}}
{{- define "asibot.secretName" -}}
{{- if .Values.existingSecret }}
{{- .Values.existingSecret }}
{{- else }}
{{- include "asibot.fullname" . }}
{{- end }}
{{- end }}

{{/*
Database URL — use explicit value or construct from postgresPassword.
*/}}
{{- define "asibot.databaseUrl" -}}
{{- if .Values.secrets.databaseUrl }}
{{- .Values.secrets.databaseUrl }}
{{- else }}
{{- printf "postgresql://asibot:%s@%s-pgbouncer:6432/asibot" .Values.secrets.postgresPassword (include "asibot.fullname" .) }}
{{- end }}
{{- end }}
