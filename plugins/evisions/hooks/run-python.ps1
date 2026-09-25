# Fail-closed Windows launcher for the evisions plugin's Python hooks under Codex CLI.
#
# Why it exists: on Windows Codex runs a hook through cmd.exe and lets the tool call through when the
# hook cannot start, exits with any code other than 2, or exits 2 with an empty stderr. A safety gate
# registered as `py -3 hook.py` therefore fails OPEN on a machine without Python, and `bash ...` fails
# open wherever Git Bash is not on cmd's PATH. This launcher needs only Windows PowerShell 5.1, which
# every supported Windows ships. It runs the script with the first interpreter that really runs
# Python 3.9 or newer (py -3, then python, then python3; each one is executed, because `python` is often
# a Microsoft Store stub that is found but only prints an install hint), and when it cannot run the
# check at all it writes an `evisions safety:` message to stderr and exits 2, which blocks the call.
#
# Usage: run-python.ps1 [-Advisory] <script> [args...]
#   <script> is absolute, or relative to the plugin root (for example hooks/bash_safety.py).
#   -Advisory is for hooks that must never block (SessionStart, UserPromptSubmit): a failure exits 1,
#   which Codex reports as a hook failure without blocking, because exit 2 there would block every
#   prompt of the session.
#   The hook input on stdin reaches Python byte for byte; stdout, stderr and the exit code pass through.
#
# No param() block on purpose: arguments such as --runtime reach $args unchanged. ASCII only, because
# Windows PowerShell 5.1 reads a file without a byte order mark in the system code page.

$ErrorActionPreference = 'Stop'

$advisory = $false
$rest = @($args)
if ($rest.Count -gt 0 -and $rest[0] -eq '-Advisory') {
    $advisory = $true
    $rest = @($rest | Select-Object -Skip 1)
}

# Ends the hook. The safety check blocks the tool call (exit 2 with the reason on stderr); an advisory
# hook reports the failure without blocking anything (exit 1).
function Stop-Launcher([string]$Reason, [string]$Until) {
    if ($advisory) {
        [Console]::Error.WriteLine("evisions safety: an evisions hook could not run because $Reason It runs again once $Until.")
        exit 1
    }
    [Console]::Error.WriteLine("evisions safety: the safety check could not run because $Reason The tool call is blocked until $Until. Tell the user this in plain words and do not try to work around it: nothing may run unchecked while the safety check is down.")
    exit 2
}

# Quotes one argument for the Windows command line (the rules Python's own parser applies).
function ConvertTo-Argument([string]$Value) {
    if ($Value -ne '' -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}

# Runs a program with the given stdin bytes and returns its exit code. With $Capture its output is read
# and dropped (the interpreter probe); without it stdout and stderr are inherited, so the hook's output
# reaches Codex unchanged.
function Invoke-Program([string]$File, [string[]]$Arguments, [byte[]]$InputBytes, [bool]$Capture) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $File
    $info.Arguments = (@($Arguments | ForEach-Object { ConvertTo-Argument $_ }) -join ' ')
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $Capture
    $info.RedirectStandardError = $Capture
    $process = [System.Diagnostics.Process]::Start($info)
    if ($InputBytes.Length -gt 0) {
        $process.StandardInput.BaseStream.Write($InputBytes, 0, $InputBytes.Length)
    }
    $process.StandardInput.Close()
    if ($Capture) {
        $null = $process.StandardOutput.ReadToEndAsync()
        $null = $process.StandardError.ReadToEnd()
    }
    $process.WaitForExit()
    return $process.ExitCode
}

try {
    if ($rest.Count -lt 1 -or [string]::IsNullOrEmpty([string]$rest[0])) {
        Stop-Launcher 'no script was named.' 'the plugin is updated or reinstalled'
    }
    $pluginRoot = Split-Path -Parent $PSScriptRoot
    $script = [string]$rest[0]
    if (-not [System.IO.Path]::IsPathRooted($script)) {
        $script = Join-Path $pluginRoot $script
    }
    if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
        Stop-Launcher "its script is missing ($script)." 'the plugin is updated or reinstalled'
    }
    $scriptArgs = @($rest | Select-Object -Skip 1)

    # The hook input is read before any probe runs, so no child process can consume it.
    $stdin = [Console]::OpenStandardInput()
    $buffer = New-Object System.IO.MemoryStream
    $stdin.CopyTo($buffer)
    $inputBytes = $buffer.ToArray()

    $probe = 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'
    $python = $null
    $pythonPrefix = @()
    foreach ($candidate in @('py -3', 'python', 'python3')) {
        $parts = $candidate.Split(' ')
        $found = Get-Command $parts[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $found) {
            continue
        }
        $prefix = @($parts | Select-Object -Skip 1)
        try {
            $code = Invoke-Program $found.Path (@($prefix) + @('-S', '-c', $probe)) ([byte[]]@()) $true
        } catch {
            continue
        }
        if ($code -eq 0) {
            $python = $found.Path
            $pythonPrefix = $prefix
            break
        }
    }
    if ($null -eq $python) {
        Stop-Launcher 'Python 3 (3.9 or newer) was not found; tried py -3, python and python3.' 'Python 3 is installed'
    }

    $code = Invoke-Program $python (@($pythonPrefix) + @('-S', $script) + $scriptArgs) $inputBytes $false
    exit $code
} catch {
    Stop-Launcher "the launcher hit an error ($($_.Exception.Message))." 'the plugin is updated or reinstalled'
}
