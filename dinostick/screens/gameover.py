"""Game over: team score, run stats, cause of death, rematch / back to menu."""

from __future__ import annotations

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import Screen

from game import constants as C
from net import protocol
from ui import theme
from ui import format as fmt
from ui.widgets import Caption, Card, MenuButton, Panel, Stat, Title

from . import GAME, MENU


class GameOverScreen(Screen):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        root = Panel(columns=2)

        # -- left: what just happened ---------------------------------------
        #
        # The score is the headline; distance and time are the supporting
        # facts. Reading order was the other way round before, with the score
        # sharing a size with everything else on the screen.
        self.headline = Title("RUN OVER", theme.FONT_TITLE)
        root.col_left.add_widget(self.headline)

        self.score_card = Card()
        self.score_stat = Stat("Team score", "0", value_size=theme.FONT_DISPLAY,
                               value_color=theme.ACCENT)
        self.score_card.add_widget(self.score_stat)

        stats_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                              spacing=theme.GAP)
        self.distance_stat = Stat("Distance", "-", value_size=theme.FONT_BODY)
        self.time_stat = Stat("Time", "-", value_size=theme.FONT_BODY)
        self.team_stat = Stat("Team", "-", value_size=theme.FONT_BODY)
        for stat in (self.distance_stat, self.time_stat, self.team_stat):
            stats_row.add_widget(stat)
        stats_row.height = self.distance_stat.height
        self.score_card.add_widget(stats_row)
        root.col_left.add_widget(self.score_card)

        self.cause_label = Caption("", theme.FONT_SMALL, color=theme.DANGER)
        root.col_left.add_widget(self.cause_label)

        # -- right: what to do next -----------------------------------------
        self.rematch_button = MenuButton("PLAY AGAIN", self._rematch)
        root.col_right.add_widget(self.rematch_button)
        root.col_right.add_widget(MenuButton("Back to Menu", self._menu,
                                         variant="secondary"))
        self.add_widget(root)

    def on_pre_enter(self, *_args) -> None:
        # Only the host can call a rematch -- clients wait to be told.
        app = App.get_running_app()
        is_client = app is not None and app.mode == "client"
        self.rematch_button.disabled = is_client
        self.rematch_button.text = ("Waiting for the host..." if is_client
                                    else "PLAY AGAIN")

    def show_result(self, score: int, distance: float,
                    cause_player_id: int | None = None,
                    cause_obstacle: str | None = None,
                    players: int = 1,
                    seconds: float | None = None) -> None:
        self.score_stat.set_value(fmt.score(score))
        self.distance_stat.set_value(fmt.distance(distance))
        self.time_stat.set_value("-" if seconds is None
                                 else fmt.duration(seconds))
        self.team_stat.set_value(f"{players} dino"
                                 f"{'' if players == 1 else 's'}")

        if cause_obstacle is None:
            self.cause_label.text = ""
            return

        what = C.OBSTACLE_LABELS.get(cause_obstacle, cause_obstacle)
        if players > 1 and cause_player_id is not None:
            self.cause_label.text = f"Player {cause_player_id + 1} hit {what}"
        else:
            self.cause_label.text = f"Crashed into {what}"

    def _rematch(self) -> None:
        app = App.get_running_app()
        if app is not None and app.mode == "host" and app.host is not None:
            # Fresh seed, so a rematch is a genuinely new world -- and a fresh
            # session on every device, built from it.
            app.host.broadcast(protocol.rematch())
            app.host_start_game()
        self.manager.current = GAME

    def _menu(self) -> None:
        app = App.get_running_app()
        if app is not None:
            app.leave_network()
        self.manager.current = MENU
