"""Kivy screens registered on the app's ScreenManager.

MENU is built during ``build()`` and is what the player sees first. The rest
are constructed one frame later (see ``DinoStickApp.on_start``) so that nothing
but the menu stands between app launch and the first drawn frame -- on Android
the presplash stays up for precisely that long.
"""

MENU = "menu"
LOBBY = "lobby"
GAME = "game"
GAMEOVER = "gameover"
