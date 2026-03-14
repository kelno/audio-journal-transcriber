# Sync voice recordings from phone

- Recording your voice from your phone using any app that can record in an open directory (such as https://github.com/FossifyOrg/Voice-Recorder)
  - Such as `/storage/emulated/0/Android/media/voice_recordings/`
- Sync that directory with whatever server/computer will do the processing
  - For example using Syncthing
- Have this transcriber tool running in deamon mode (`--daemon`), watching the synced directory as the "input directory" 
(you might have issues with file permissions, maybe run transcriber as the same user as Syncthing)
