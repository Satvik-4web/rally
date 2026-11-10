Add-Type -AssemblyName System.Speech
try {
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
    $grammar = New-Object System.Speech.Recognition.DictationGrammar
    $engine.LoadGrammar($grammar)
    $engine.SetInputToDefaultAudioDevice()
    Write-Host "SUCCESS: SetInputToDefaultAudioDevice connected successfully!"
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
}
