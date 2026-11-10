import subprocess
import sys

ps_script = """
Add-Type -AssemblyName System.Speech
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$grammar = New-Object System.Speech.Recognition.DictationGrammar
$engine.LoadGrammar($grammar)
$engine.SetInputToDefaultAudioDevice()

Register-ObjectEvent -InputObject $engine -EventName SpeechHypothesized -Action {
    Write-Host "[HYPOTHESIS]: $($Event.SourceEventArgs.Result.Text)"
} | Out-Null

Register-ObjectEvent -InputObject $engine -EventName SpeechRecognized -Action {
    Write-Host "[FINAL]: $($Event.SourceEventArgs.Result.Text)"
} | Out-Null

Write-Host "[READY] SAPI Dictation Engine started. Speak into your microphone..."
$engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
Start-Sleep -Seconds 6
$engine.RecognizeAsyncStop()
Write-Host "[DONE] Finished test."
"""

res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], capture_output=True, text=True)
print("STDOUT:", res.stdout)
print("STDERR:", res.stderr)
