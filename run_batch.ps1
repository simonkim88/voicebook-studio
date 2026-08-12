# run_batch.ps1 - 야간 배치 변환을 세션과 분리해서 시작한다 (Windows).
#
#   .\run_batch.ps1                    # $Src 의 기본 파일 목록
#   .\run_batch.ps1 a.txt b.txt        # 변환할 파일을 직접 지정
#
# macOS / Linux 에서는 run_batch.sh 를 쓰세요.
#
# - 슬립 방지: SetThreadExecutionState (powercfg 설정을 건드리지 않음)
# - 창 없는 백그라운드 프로세스로 띄우므로 터미널을 닫아도 계속 실행됩니다
# - 이미 돌고 있으면 두 번 띄우지 않습니다

# PositionalBinding=$false: 이게 없으면 위치 인자가 $Files 가 아니라 그다음 파라미터
# ($Python)에 붙어버린다. 파일 목록만 위치 인자로 받고 나머지는 이름 지정 전용.
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Files,

    [string]$Python  = $env:VOICEBOOK_PYTHON,
    [string]$Src     = $(if ($env:VOICEBOOK_SRC)   { $env:VOICEBOOK_SRC }   else { "$env:USERPROFILE\Documents\Audiobooks" }),
    [string]$Voice   = $(if ($env:VOICEBOOK_VOICE) { $env:VOICEBOOK_VOICE } else { "Ryan" }),
    [string]$Model   = $(if ($env:VOICEBOOK_MODEL) { $env:VOICEBOOK_MODEL } else { "0.6B" }),
    [int]$MaxRetries = 20
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------------------------------------------------------------- 파이썬 찾기
function Find-Python {
    if ($Python) { return $Python }
    $candidates = @(
        "$Repo\venv\Scripts\python.exe",
        "$Repo\.venv\Scripts\python.exe",
        "$env:USERPROFILE\miniconda3\envs\qwen3-tts\python.exe",
        "$env:USERPROFILE\anaconda3\envs\qwen3-tts\python.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$PythonExe = Find-Python
if (-not $PythonExe) {
    Write-Host "[X] 파이썬을 찾을 수 없습니다. -Python 또는 VOICEBOOK_PYTHON 을 지정하세요." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- 경로
$LogDir = if ($env:LOCALAPPDATA) { "$env:LOCALAPPDATA\VoiceBookStudio\Logs" } else { "$env:USERPROFILE\VoiceBookStudio\Logs" }
$Log = if ($env:VOICEBOOK_BATCH_LOG) { $env:VOICEBOOK_BATCH_LOG } else { Join-Path $LogDir 'VoiceBookBatch.log' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Log) | Out-Null

if (-not $Files -or $Files.Count -eq 0) {
    $Files = @(
        "$Src\Why the World Does Not Exist Part 2-3.txt",
        "$Src\Why the World Does Not Exist Part 4.txt",
        "$Src\Why the World Does Not Exist Part 5.txt",
        "$Src\Why the World Does Not Exist Part 6-7.txt"
    )
}

# ---------------------------------------------------------------- 중복 실행 방지
$running = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*batch_runner.py*' }
if ($running) {
    Write-Host "[X] 배치가 이미 실행 중입니다 (PID $($running.ProcessId -join ' '))" -ForegroundColor Red
    Write-Host "    진행 상황: Get-Content -Wait `"$Log`""
    exit 1
}

# ---------------------------------------------------------------- 감시 루프
# 러너가 예기치 않게 죽으면 자동으로 다시 시작한다. 완성된 파일(.mp3+.srt)은
# 건너뛰므로 재시작해도 이어서 진행된다. 정상 종료(exit 0)면 루프를 빠져나온다.
$worker = @'
param($Python, $Repo, $Log, $Voice, $Model, $MaxRetries, $FileList)

# 파일 목록은 명령줄이 아니라 파일로 받는다. powershell.exe -File 로 스크립트를
# 부르면 배열 파라미터에 첫 원소만 바인딩되고 나머지는 조용히 버려진다.
$Files = @(Get-Content -LiteralPath $FileList -Encoding UTF8 | Where-Object { $_.Trim() })

# 파이썬 출력을 PowerShell 파이프(*>>)로 받으면 안 된다. PS 5.1이 cp949로 다시
# 인코딩하면서 UTF-8 로그를 깨뜨린다. 자식 프로세스가 파일에 직접 쓰게 하고,
# 그 결과를 바이트 그대로 이어붙인다.
$env:PYTHONIOENCODING = 'utf-8'

function Append-Raw($Src, $Dst) {
    if (-not (Test-Path -LiteralPath $Src)) { return }
    $bytes = [System.IO.File]::ReadAllBytes($Src)
    if ($bytes.Length) {
        $fs = [System.IO.File]::Open($Dst, 'Append', 'Write', 'Read')
        $fs.Write($bytes, 0, $bytes.Length); $fs.Close()
    }
    Remove-Item -LiteralPath $Src -ErrorAction SilentlyContinue
}

function Append-Line($Text, $Dst) {
    $line = "[{0}] {1}`r`n" -f (Get-Date -Format 'MM-dd HH:mm:ss'), $Text
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($line)
    $fs = [System.IO.File]::Open($Dst, 'Append', 'Write', 'Read')
    $fs.Write($bytes, 0, $bytes.Length); $fs.Close()
}

# 작업 중 절전/디스플레이 꺼짐 방지 (ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)
$sig = '[DllImport("kernel32.dll", SetLastError = true)] public static extern uint SetThreadExecutionState(uint esFlags);'
$k32 = Add-Type -MemberDefinition $sig -Name 'PowerMgr' -Namespace 'VoiceBook' -PassThru
[void]$k32::SetThreadExecutionState(0x80000000 -bor 0x00000001 -bor 0x00000040)

$outFile = Join-Path $env:TEMP 'voicebook_batch.out'
$errFile = Join-Path $env:TEMP 'voicebook_batch.err'

# 콘솔 찌꺼기는 메인 로그와 분리한다. 파이썬 자신은 UTF-8로 쓰지만, 라이브러리가
# 내부에서 부르는 외부 프로그램(sox 등)은 cp949로 뱉기 때문에 섞으면 메인 로그가
# 깨진다. 메인 로그는 batch_runner.py 가 직접 쓰는 UTF-8만 유지한다.
$ConsoleLog = [System.IO.Path]::ChangeExtension($Log, $null) + 'console.log'

try {
    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        if ($attempt -gt 1) {
            Append-Line "[retry] 러너가 종료됨 - 재시작 ($attempt/$MaxRetries)" $Log
        }
        # Start-Process 는 인자를 그냥 공백으로 잇는다 → 공백 있는 경로를 위해 직접 따옴표.
        $argList = @("$Repo\batch_runner.py", '--voice', $Voice, '--model-size', $Model) + $Files |
            ForEach-Object { '"' + $_ + '"' }
        $p = Start-Process -FilePath $Python -ArgumentList $argList -NoNewWindow -Wait -PassThru `
                 -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        Append-Raw $outFile $ConsoleLog
        Append-Raw $errFile $ConsoleLog
        if ($p.ExitCode -eq 0) { break }
        Append-Line "러너 종료 코드 $($p.ExitCode) — 콘솔 출력: $ConsoleLog" $Log
        Start-Sleep -Seconds 30
    }
} finally {
    [void]$k32::SetThreadExecutionState(0x80000000)  # ES_CONTINUOUS 만 = 원상복구
}
'@

$workerPath = Join-Path $env:TEMP 'voicebook_batch_worker.ps1'
# BOM 필수: Windows PowerShell 5.1 은 BOM 없는 .ps1 을 ANSI(cp949)로 읽어
# 한글 주석이 깨지면서 파싱 에러가 난다.
[System.IO.File]::WriteAllText($workerPath, $worker, (New-Object System.Text.UTF8Encoding $true))

$fileListPath = Join-Path $env:TEMP 'voicebook_batch_files.txt'
[System.IO.File]::WriteAllLines($fileListPath, [string[]]$Files, (New-Object System.Text.UTF8Encoding $true))

# -WindowStyle Hidden + 부모와 분리 → 터미널을 닫아도 살아남습니다.
# 각 인자를 직접 따옴표로 감싼다: Start-Process 는 -ArgumentList 배열을 공백으로
# 잇기만 해서, 공백이 든 경로(저장소·로그)가 두 개로 쪼개진다.
$psArgs = @(
    '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', $workerPath,
    '-Python', $PythonExe, '-Repo', $Repo, '-Log', $Log,
    '-Voice', $Voice, '-Model', $Model, '-MaxRetries', $MaxRetries,
    '-FileList', $fileListPath
) | ForEach-Object { '"' + $_ + '"' }

Start-Process -FilePath 'powershell.exe' -ArgumentList $psArgs -WindowStyle Hidden | Out-Null

Start-Sleep -Seconds 3
$started = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*batch_runner.py*' }
if ($started) {
    Write-Host "[OK] 배치 시작됨 (PID $($started.ProcessId -join ' '))" -ForegroundColor Green
} else {
    Write-Host "[..] 배치 프로세스를 띄웠습니다 (파이썬 기동 대기 중)" -ForegroundColor Yellow
}
Write-Host "     파이썬: $PythonExe"
Write-Host "     진행 상황: Get-Content -Wait `"$Log`""
