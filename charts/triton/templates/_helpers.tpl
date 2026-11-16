{{- define "triton.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "triton.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 58 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "triton.name" .) | trunc 58 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "triton.grpcServiceName" -}}
{{- printf "%s-grpc" (include "triton.fullname" .) }}
{{- end }}

{{- define "triton.dockerconfigSecretName" -}}
{{- printf "%s-dockerconfig" (include "triton.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "triton.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "triton.selectorLabels" -}}
app.kubernetes.io/name: {{ include "triton.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "triton.labels" -}}
application: triton
helm.sh/chart: {{ include "triton.chart" . }}
{{- if .Values.apolo_app_id }}
platform.apolo.us/app-id: {{ .Values.apolo_app_id | quote }}
{{- end }}
{{ include "triton.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "triton.progressDeadlineSeconds" -}}
{{- $startup := .Values.probe.startup }}
{{- add (mul $startup.periodSeconds $startup.failureThreshold) .Values.progressDeadlineMarginSeconds }}
{{- end }}
