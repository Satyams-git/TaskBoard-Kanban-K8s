{{/* Base name for every object: the release name, e.g. "taskboard" */}}
{{- define "taskboard.fullname" -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels shared by all objects */}}
{{- define "taskboard.labels" -}}
app.kubernetes.io/part-of: taskboard
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{/* Selector labels for one component. Call with (dict "ctx" . "component" "backend") */}}
{{- define "taskboard.selectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
{{- end -}}

{{/* Full image name: <imageUser>/<name>:<tag> */}}
{{- define "taskboard.image" -}}
{{- printf "%s/%s:%s" .ctx.Values.imageUser .image.name .image.tag -}}
{{- end -}}
