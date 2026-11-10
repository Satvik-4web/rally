import subprocess

ps = """
Add-Type -AssemblyName System.Speech
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$grammar = New-Object System.Speech.Recognition.DictationGrammar
$engine.LoadGrammar($grammar)
Write-Host "SetInputToWaveStream is method: $([bool]($engine.GetType().GetMethod('SetInputToWaveStream')))"
"""

res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], capture_output=True, text=True)
print("STDOUT:", res.stdout)
print("STDERR:", res.stderr)
