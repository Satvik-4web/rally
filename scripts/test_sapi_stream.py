import subprocess
import threading
import time

ps_code = """
Add-Type -AssemblyName System.Speech
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$grammar = New-Object System.Speech.Recognition.DictationGrammar
$engine.LoadGrammar($grammar)
$engine.SetInputToDefaultAudioDevice()

Register-ObjectEvent -InputObject $engine -EventName SpeechHypothesized -Action {
    [Console]::WriteLine("HYPOTHESIS:" + $Event.SourceEventArgs.Result.Text)
} | Out-Null

Register-ObjectEvent -InputObject $engine -EventName SpeechRecognized -Action {
    [Console]::WriteLine("FINAL:" + $Event.SourceEventArgs.Result.Text)
} | Out-Null

[Console]::WriteLine("LISTENER_STARTED")
$engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)

while ($true) {
    Start-Sleep -Milliseconds 100
}
"""

proc = subprocess.Popen(
    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)

print("Started proc PID:", proc.pid)
# Read first line
line = proc.stdout.readline()
print("First line from listener:", line.strip())

proc.terminate()
print("Terminated cleanly.")
