"""Audio to transcript.

Transcribe (faster-whisper, Whisper large-v3-turbo converted by Auris),
work out who spoke when (one file per speaker, or Silero VAD + SpeechBrain
speaker embeddings + clustering for a single room microphone), and decide
who is who (clinician, patient, or unknown when not sure; a person decides
in review). The result is an ordinary timed :class:`~auris.transcript.Transcript`,
so every later stage is unchanged.

The heavy libraries live in the optional ``speech`` extra and are imported
only when audio is processed.
"""
