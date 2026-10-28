# TDesktop layer229 collectible verification colors

The profile top-bar badge styles must use the pinned collectible gift's foreground colors. `info_profile_top_bar.cpp` is the patched TDesktop 7.2.5 source file. The implementation forces badge style override while a collectible is active and sets the bot verification emoji's foreground to the collectible `textColor` (while the regular verification badge follows the gift edge/center blend).

This source change has not been compiled into a Windows executable in this environment. The existing `clients/Telegram-projectre/Telegram.exe` is therefore not updated by these files.
