# Synthesize docs/pitch/narration.txt with Windows System.Speech, one WAV per segment (44.1 kHz, 16-bit, mono),
# plus <id>.words.json with the audio offset of every spoken word (used to pace the recorded-run segment).
# "raw_ms" is SpeakProgress.AudioPosition as reported. On this engine it counts 16 kHz time whatever the output rate
# (checked against ffmpeg silencedetect on the sentence pauses), so build_video.py uses raw_ms * 16000 / 44100.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File docs/pitch/video/tts.ps1 -OutDir <dir>
param(
  [Parameter(Mandatory = $true)][string]$OutDir,
  [string]$Narration = "",
  [string]$Voice = "Microsoft David Desktop",
  [int]$Rate = 0
)
$ErrorActionPreference = "Stop"
if (-not $Narration) {
  $Narration = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\narration.txt"
}
New-Item -ItemType Directory -Force $OutDir | Out-Null
Add-Type -AssemblyName System.Speech
Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Speech.AudioFormat;
using System.Speech.Synthesis;
public static class NarrationTts {
  public static string Speak(string voice, int rate, string text, string wavPath) {
    var words = new List<string>();
    using (var s = new SpeechSynthesizer()) {
      s.SelectVoice(voice);
      s.Rate = rate;
      s.SetOutputToWaveFile(wavPath, new SpeechAudioFormatInfo(44100, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
      s.SpeakProgress += (o, e) => {
        lock (words) {
          words.Add(String.Format(CultureInfo.InvariantCulture,
            "{{\"raw_ms\":{0},\"char\":{1},\"text\":\"{2}\"}}",
            (long)e.AudioPosition.TotalMilliseconds, e.CharacterPosition,
            e.Text.Replace("\\", "\\\\").Replace("\"", "\\\"")));
        }
      };
      s.Speak(text);
      s.SetOutputToNull();
    }
    return "[" + String.Join(",", words) + "]";
  }
}
"@
foreach ($line in [System.IO.File]::ReadAllLines((Resolve-Path $Narration), [System.Text.Encoding]::UTF8)) {
  if (-not $line.Contains("|")) { continue }
  $id, $text = $line.Split("|", 2)
  $wav = Join-Path $OutDir "$id.wav"
  $json = [NarrationTts]::Speak($Voice, $Rate, $text, $wav)
  [System.IO.File]::WriteAllText((Join-Path $OutDir "$id.words.json"), $json, (New-Object System.Text.UTF8Encoding $false))
  Write-Output ("{0}: {1} words" -f $id, ($text -split "\s+").Count)
}
