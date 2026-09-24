# Refresh aegispilot/ecr-pull from your laptop (AWS profile aegispilot).
# Run every ~6–10h or whenever pods show ImagePullBackOff.
$ErrorActionPreference = "Stop"
$env:AWS_PROFILE = if ($env:AWS_PROFILE) { $env:AWS_PROFILE } else { "aegispilot" }
if (-not $env:KUBECONFIG) {
  Write-Error "Set KUBECONFIG to your cluster kubeconfig path first."
}

$registry = "850887971586.dkr.ecr.ap-south-1.amazonaws.com"
$token = aws ecr get-login-password --region ap-south-1 --profile $env:AWS_PROFILE
$auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("AWS:$token"))
$dockerconfig = "{`"auths`":{`"$registry`":{`"username`":`"AWS`",`"password`":`"$token`",`"auth`":`"$auth`"}}}"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($dockerconfig))
@"
apiVersion: v1
kind: Secret
metadata:
  name: ecr-pull
  namespace: aegispilot
type: kubernetes.io/dockerconfigjson
data:
  .dockerconfigjson: $b64
"@ | kubectl apply -f -

Write-Host "ecr-pull refreshed (token ~12h). Optional: kubectl -n aegispilot rollout restart deploy"
