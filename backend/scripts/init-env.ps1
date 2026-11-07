param([string]$Path = (Join-Path $PSScriptRoot '..\..\.env'))
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $Path) { throw 'Environment file exists; preserve its credentials.' }
$template = Get-Content -Raw (Join-Path $PSScriptRoot '..\.env.example')
foreach ($name in @('POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'LOGIN_SIGNING_KEY',
    'TASK_TOKEN_SIGNING_KEY', 'TOOL_SERVICE_KEY', 'DEMO_DISPATCHER_PASSWORD', 'DEMO_DRIVER_PASSWORD')) {
    $random = [byte[]]::new(32)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    $generator.GetBytes($random)
    $generator.Dispose()
    $secret = [BitConverter]::ToString($random).Replace('-', '').ToLowerInvariant()
    $template = $template.Replace("${name}=", "${name}=${secret}")
}
$resolved = [IO.Path]::GetFullPath($Path)
[IO.File]::WriteAllText($resolved, $template, [Text.UTF8Encoding]::new($false))
Write-Output "Created private environment file: $resolved (values omitted)"
