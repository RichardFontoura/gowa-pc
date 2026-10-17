#!/usr/bin/env python3
"""Tk launcher for the God of War: Ascension PC port.

Shows a window with pages to edit the launcher settings (``settings.json``,
see :mod:`settings`) and the shared game options (``game.ini``, see
:mod:`game_ini`), backs up the save data before each run, starts the game
through :class:`run_log.RunLog` and follows its log live. All work happens
on the Tk main loop: a 100 ms timer (:meth:`Launcher.tick`) polls the game
process and its log; no threads are used.

Run without arguments for the GUI, or with ``--play`` to start the game
directly with the saved configuration (the launcher process is replaced by
the game command).
"""

import argparse
import copy
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import game_ini
from run_log import RunLog, PREFIX, classify
from keynames import sdl_key_name, sdl_mouse_name
from save_backup import backup, label as backup_label, list_backups, restore
from settings import (PORT, CONFIG_FILE, CONFIG_DIR, GAME_INI, BACKUP_DIR, SAVEDATA_DIR, CONTROLS,
                      PAD_NAMES, DIAGNOSTICS, PARALLEL_PRESETS, RESERVED_KEYS, load, save,
                      game_environment, game_command, tool_available, duplicate_bindings,
                      stick_conflicts, clear_shader_cache, SHADER_CACHE_DIR)

# Color palette of the window.
BG, PANEL, CARD, LINE = '#0d0b0b', '#161212', '#1d1717', '#2a2222'
TEXT, MUTED, ACCENT, ACCENT_HI = '#e6e0d8', '#8a817a', '#a4161a', '#c81e25'
# Page titles in sidebar order; each must have a build_* method creating it.
PAGES = ('Início', 'Jogo', 'Gráficos', 'Controles', 'Avançado', 'Diagnóstico', 'Log')
# Maximum number of lines kept in the Log page's text widget.
LOG_LIMIT = 6000
# (pattern, status text) pairs: a game output line matching a pattern updates the
# status bar; regex groups are substituted into the text with str.format.
STATUS_MARKERS = (
    (re.compile(r'^erro: (.*)'), 'Erro ao iniciar: {0}'),
    (re.compile(r'^\[boot\] VFS root'), 'Carregando o jogo…'),
    (re.compile(r'^\[boot\] dispatching entry'), 'Iniciando o jogo…'),
    (re.compile(r'^\[rsx\] .*backend init OK'), 'Jogo aberto'),
    (re.compile(r'^\[rsx\] .*backend init FAILED'), 'Falha ao abrir a janela do jogo'),
)
# Value -> label tables for the comboboxes built with Launcher.choice().
DISPLAY_LABELS = {'windowed': 'Janela', 'borderless': 'Tela cheia sem bordas', 'fullscreen': 'Tela cheia'}
SIZE_LABELS = {'1280x720': '1280 × 720', '1600x900': '1600 × 900', '1920x1080': '1920 × 1080',
               'monitor': 'Tamanho do monitor'}
CORNER_LABELS = {'tl': 'Superior esquerdo', 'tr': 'Superior direito',
                 'bl': 'Inferior esquerdo', 'br': 'Inferior direito'}
ITEM_LABELS = {'fps': 'FPS', 'frametime': 'Tempo de quadro', 'graph': 'Gráfico',
               'present': 'Apresentação', 'spu': 'SPU', 'shaders': 'Shaders',
               'video': 'Vídeo'}
FLIP_LABELS = {'250': 'Padrão', '500': 'Conservador', '1500': 'Muito conservador',
               '0': 'Desligado (pode travar)'}
PARALLEL_LABELS = {'Padrão': 'Padrão (7,13,14,15,16)', 'Seguro': 'Seguro (tudo em série)',
                   'custom': 'Personalizado'}


def status_for_line(line):
    """Return the status bar text for a log line, or ``None`` if it is not a milestone.

    The ``session.log`` timestamp prefix is removed before matching against
    ``STATUS_MARKERS``.
    """
    line = PREFIX.sub('', line)
    for pattern, text in STATUS_MARKERS:
        match = pattern.search(line)
        if match:
            return text.format(*match.groups())
    return None


def open_path(path):
    """Create the directory ``path`` if needed and open it with ``xdg-open``.

    ``xdg-open`` runs detached in its own session with its output discarded.

    Raises:
        OSError: If the directory cannot be created or ``xdg-open`` cannot be started.
    """
    Path(path).mkdir(parents=True, exist_ok=True)
    subprocess.Popen(['xdg-open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def backup_saves():
    """Back up the save data before a run and describe the result.

    Copies ``SAVEDATA_DIR`` into ``BACKUP_DIR`` (keeping the newest copies,
    see :func:`save_backup.backup`). A failure is reported, not raised, so
    the game still starts.

    Returns:
        A ``[launcher]`` log line (in Portuguese) for the run log.
    """
    try:
        made = backup(SAVEDATA_DIR, BACKUP_DIR)
    except OSError as e:
        return f'[launcher] falha no backup dos saves (o jogo abre mesmo assim): {e}'
    if made:
        return f'[launcher] backup dos saves: {made}'
    return '[launcher] sem saves para copiar'


class Launcher:
    """The launcher window: settings pages, save backups and the game run.

    Settings are edited through Tk variables and only written to disk by
    :meth:`save` (button "Salvar", before "JOGAR" and when the window
    closes). At most one game process runs at a time; :attr:`generation`
    is incremented for every run so delayed callbacks can tell runs apart.
    """

    def __init__(self, root):
        """Build the whole window inside ``root`` and start the poll timer.

        Imports ``tkinter`` lazily, loads ``settings.json`` and ``game.ini``,
        configures the ``clam`` ttk theme with the launcher palette, creates
        the sidebar with one button per page, the bottom bar (status text,
        "JOGAR", "Parar", "Salvar") and every page, shows "Início" and
        schedules :meth:`tick` after 100 ms. Closing the window calls
        :meth:`close`.

        Raises:
            ValueError: If ``settings.json`` is invalid.
            OSError: If the settings cannot be read.
        """
        import tkinter as tk
        from tkinter import ttk, messagebox
        self.tk, self.ttk, self.messagebox, self.root = tk, ttk, messagebox, root
        self.process = self.log_reader = self.log_writer = None
        self.run_log = None
        self.logging_error_seen = False
        self.closing = False
        self.generation = 0
        self.partial = ''
        self.capturing = None
        self.app = load()
        self.ini = game_ini.read(GAME_INI)
        self.vars, self.key_vars, self.pad_vars, self.pad_boxes = {}, {}, {}, {}
        self.ini_vars, self.item_vars, self.diag_vars = {}, {}, {}
        self.backup_names = []
        root.title('God of War: Ascension (PC)')
        root.configure(bg=BG)
        root.geometry('1180x760')
        root.minsize(900, 620)
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', background=PANEL, foreground=TEXT, fieldbackground=CARD,
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, font=('DejaVu Sans', 10))
        style.configure('TButton', background=CARD, padding=(12, 7))
        style.map('TButton', background=[('active', ACCENT)], foreground=[('disabled', MUTED)])
        style.configure('Play.TButton', background=ACCENT, font=('DejaVu Sans', 12, 'bold'))
        style.configure('TEntry', insertcolor=TEXT, padding=5)
        style.configure('Capture.TEntry', fieldbackground=ACCENT)
        style.configure('TCombobox', padding=5, arrowcolor=TEXT)
        style.map('TCombobox', fieldbackground=[('readonly', CARD)], foreground=[('readonly', TEXT)],
                  selectbackground=[('readonly', CARD)], selectforeground=[('readonly', TEXT)])
        style.configure('TCheckbutton', padding=6)
        style.map('TCheckbutton', foreground=[('disabled', MUTED)], background=[('active', PANEL)])
        root.option_add('*TCombobox*Listbox.background', CARD)
        root.option_add('*TCombobox*Listbox.foreground', TEXT)
        root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        side = tk.Frame(root, bg=BG, width=235)
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        tk.Label(side, text='GOD OF WAR', fg=TEXT, bg=BG, font=('DejaVu Serif', 20)).pack(pady=(30, 0))
        tk.Label(side, text='A S C E N S I O N', fg=ACCENT_HI, bg=BG,
                 font=('DejaVu Sans', 11, 'bold')).pack(pady=(8, 30))
        self.nav = {}
        for name in PAGES:
            button = tk.Button(side, text=name, anchor='w', bd=0, highlightthickness=0, padx=24, pady=10,
                               bg=BG, fg=TEXT, activebackground=CARD, activeforeground=TEXT,
                               command=lambda n=name: self.show(n))
            button.pack(fill='x', padx=12, pady=2)
            self.nav[name] = button
        tk.Label(side, text='PC • Linux / Vulkan\nBCUS98232 • 1.12', bg=BG, fg=MUTED,
                 justify='left').pack(side='bottom', pady=24)
        body = ttk.Frame(root)
        body.pack(side='left', expand=True, fill='both')
        bottom = ttk.Frame(body, padding=18)
        bottom.pack(side='bottom', fill='x')
        self.status = tk.StringVar(value='Pronto')
        ttk.Label(bottom, textvariable=self.status).pack(side='left')
        self.play_button = ttk.Button(bottom, text='JOGAR', style='Play.TButton', command=self.play)
        self.play_button.pack(side='right', padx=6)
        self.stop_button = ttk.Button(bottom, text='Parar', command=self.stop, state='disabled')
        self.stop_button.pack(side='right', padx=6)
        ttk.Button(bottom, text='Salvar', command=self.save).pack(side='right', padx=6)
        self.content = ttk.Frame(body)
        self.content.pack(fill='both', expand=True)
        self.pages = {}
        self.build_pages()
        self.show('Início')
        root.after(100, self.tick)

    def page(self, name, description):
        """Create a scrollable page titled ``name`` and register it in :attr:`pages`.

        The page is a canvas with a vertical scrollbar holding a padded frame;
        the frame's width follows the canvas and the scroll region follows the
        frame's size. The title and the ``description`` note are added at the
        top. The page is not shown until :meth:`show` is called.

        Returns:
            The inner frame that page builders fill with widgets.
        """
        outer = self.ttk.Frame(self.content)
        canvas = self.tk.Canvas(outer, bg=PANEL, highlightthickness=0)
        scroll = self.ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        frame = self.ttk.Frame(canvas, padding=28)
        item = canvas.create_window(0, 0, window=frame, anchor='nw')
        frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(item, width=e.width))
        self.tk.Label(frame, text=name, bg=PANEL, fg=TEXT,
                      font=('DejaVu Serif', 24), anchor='w').pack(fill='x', pady=(0, 8))
        self.note(frame, description)
        self.pages[name] = outer
        return frame

    def note(self, frame, text):
        """Pack a muted, left-aligned paragraph into ``frame`` and return its label.

        The wrap length tracks the label's width so the text reflows when the
        window is resized.
        """
        label = self.tk.Label(frame, text=text, bg=PANEL, fg=MUTED, justify='left', anchor='w',
                              wraplength=700, font=('DejaVu Sans', 10))
        label.pack(fill='x', pady=(0, 18))
        label.bind('<Configure>', lambda e: label.configure(wraplength=max(200, e.width - 8)))
        return label

    def section(self, frame, title):
        """Pack an upper-case accent-colored section heading into ``frame``."""
        self.tk.Label(frame, text=title.upper(), bg=PANEL, fg=ACCENT_HI, anchor='w',
                      font=('DejaVu Sans', 10, 'bold')).pack(fill='x', pady=(14, 4))

    def var(self, name):
        """Return the Tk variable for the top-level setting ``name``, creating it on first use.

        A ``BooleanVar`` is used for boolean settings and a ``StringVar``
        otherwise, initialized from :attr:`app`. :meth:`collect` reads every
        variable created here back into the settings.
        """
        if name not in self.vars:
            cls = self.tk.BooleanVar if isinstance(self.app[name], bool) else self.tk.StringVar
            self.vars[name] = cls(value=self.app[name])
        return self.vars[name]

    def choice(self, frame, title, var, options, command=None):
        """Add a labelled read-only combobox that edits ``var`` through display labels.

        ``var`` holds the stored value while the combobox shows the matching
        label from ``options``; a value without a label is shown as-is. The
        displayed label follows later changes to ``var``.

        Args:
            frame: Parent frame.
            title: Text shown to the left of the combobox.
            var: Tk variable holding the value.
            options: Mapping of value (as ``str``) to display label.
            command: Optional callable run after the user picks an entry.

        Returns:
            The ``ttk.Combobox``.
        """
        row = self.ttk.Frame(frame)
        row.pack(fill='x', pady=6)
        self.ttk.Label(row, text=title, width=30).pack(side='left')
        label = self.tk.StringVar(value=options.get(str(var.get()), str(var.get())))
        box = self.ttk.Combobox(row, values=list(options.values()), state='readonly', width=31,
                                textvariable=label)
        var.trace_add('write', lambda *_: label.set(options.get(str(var.get()), str(var.get()))))

        def selected(_event):
            """Store the value whose label was picked and run ``command``."""
            var.set(next(k for k, v in options.items() if v == box.get()))
            if command:
                command()
        box.bind('<<ComboboxSelected>>', selected)
        box.pack(side='left')
        return box

    def spin(self, frame, title, var, low, high, step):
        """Add a labelled spinbox bound to ``var`` (range ``low``..``high`` by ``step``) and return its row.

        The spinbox does not enforce the range on typed text; values are
        checked when the settings are validated on save.
        """
        row = self.ttk.Frame(frame)
        row.pack(fill='x', pady=6)
        self.ttk.Label(row, text=title, width=30).pack(side='left')
        self.ttk.Spinbox(row, from_=low, to=high, increment=step, textvariable=var, width=10).pack(side='left')
        return row

    def check(self, frame, text, var, enabled=True):
        """Pack a checkbutton bound to ``var`` into ``frame``, disabled when ``enabled`` is false, and return it."""
        button = self.ttk.Checkbutton(frame, text=text, variable=var, state='normal' if enabled else 'disabled')
        button.pack(anchor='w')
        return button

    def build_pages(self):
        """Create every page listed in ``PAGES``."""
        self.build_home()
        self.build_game()
        self.build_graphics()
        self.build_controls()
        self.build_advanced()
        self.build_diagnostics()
        self.build_log()

    def build_home(self):
        """Create the "Início" page: title banner and short usage notes."""
        f =self.page('Início', 'O mesmo padrão de configuração dos ports God of War no PC.')
        self.tk.Label(f, text='GOD OF WAR\nASCENSION', bg=CARD, fg=TEXT,
                      font=('DejaVu Serif', 32), pady=45).pack(fill='x', pady=20)
        self.note(f, 'Continue a jornada de Kratos. Configure seus controles antes de jogar.\n\n'
                     'No jogo, Insert (ou R3+L2 no controle) abre o menu do PC e F10 liga o contador de FPS.\n\n'
                     'Port em desenvolvimento: a campanha completa ainda não foi validada.')

    def build_game(self):
        """Create the "Jogo" page.

        Reports whether the built executable, the decrypted ELF, the extracted
        disc and the update are present (checked once, when the page is
        built), and adds the save backup controls: a combobox of backups, a
        "Restaurar" button (:meth:`restore_saves`) and a button that opens
        the savedata folder.
        """
        f =self.page('Jogo', 'Estado da instalação e backups dos saves.')
        for title, path in (('Executável', PORT / 'build/linux-vulkan/GowAscensionRecomp'),
                            ('Jogo', PORT / 'game/EBOOT.ELF'), ('Disco', PORT / 'game/disc/PS3_GAME'),
                            ('Atualização', PORT / 'game/update')):
            self.note(f, f'{title}: {"encontrado" if path.exists() else "ausente"}\n{path}')
        self.note(f, 'Esta versão usa a instalação já extraída. O assistente de importação será adicionado depois.')
        self.section(f, 'Saves')
        self.note(f, f'Antes de cada execução o launcher copia os saves para {BACKUP_DIR} e guarda as '
                     f'10 cópias mais recentes. Restaurar uma cópia faz antes um backup dos saves atuais.\n'
                     f'Saves: {SAVEDATA_DIR}')
        row = self.ttk.Frame(f)
        row.pack(fill='x', pady=6)
        self.ttk.Label(row, text='Backup', width=12).pack(side='left')
        self.backup_box = self.ttk.Combobox(row, state='readonly', width=28)
        self.backup_box.pack(side='left', padx=(0, 8))
        self.restore_button = self.ttk.Button(row, text='Restaurar', command=self.restore_saves)
        self.restore_button.pack(side='left', padx=4)
        self.ttk.Button(row, text='Abrir pasta de saves',
                        command=lambda: self.open_folder(SAVEDATA_DIR)).pack(side='left', padx=4)
        self.refresh_backups()

    def build_graphics(self):
        """Create the "Gráficos" page, which edits ``game.ini`` options.

        Creates :attr:`ini_vars` (one Tk variable per ``game.ini`` key except
        ``overlay_items``) and :attr:`item_vars` (one boolean per known
        overlay item), fills them with :meth:`apply_ini`, and adds the display,
        FPS overlay and external tool (MangoHud, GameMode) controls. A tool
        that is not installed is shown disabled.
        """
        f =self.page('Gráficos', 'Gravado em game.ini, o mesmo arquivo do menu do jogo. Vale na próxima execução.')
        self.ini_vars = {
            'display_mode': self.tk.StringVar(), 'window_size': self.tk.StringVar(),
            'vsync': self.tk.BooleanVar(), 'show_fps': self.tk.BooleanVar(),
            'overlay_corner': self.tk.StringVar(), 'overlay_scale': self.tk.StringVar(),
            'overlay_opacity': self.tk.StringVar(),
        }
        self.item_vars = {item: self.tk.BooleanVar() for item in game_ini.OVERLAY_ITEMS}
        self.size_options = dict(SIZE_LABELS)
        self.apply_ini()
        self.section(f, 'Exibição')
        self.choice(f, 'Modo de exibição', self.ini_vars['display_mode'], DISPLAY_LABELS)
        self.size_box = self.choice(f, 'Tamanho da janela', self.ini_vars['window_size'], self.size_options)
        self.check(f, 'Sincronização vertical (VSync)', self.ini_vars['vsync'])
        self.note(f, 'A imagem do jogo é gerada em 1280 × 720; tamanho da janela e tela cheia ajustam a '
                     'apresentação. Upscaling e efeitos extras ainda não estão disponíveis.')
        self.section(f, 'Contador de FPS')
        self.check(f, 'Mostrar contador de FPS', self.ini_vars['show_fps'])
        self.choice(f, 'Canto', self.ini_vars['overlay_corner'], CORNER_LABELS)
        self.spin(f, 'Escala (%)', self.ini_vars['overlay_scale'], 50, 300, 10)
        self.spin(f, 'Opacidade (%)', self.ini_vars['overlay_opacity'], 0, 100, 5)
        row = self.ttk.Frame(f)
        row.pack(fill='x', pady=6)
        self.ttk.Label(row, text='Itens', width=30).pack(side='left')
        for item in game_ini.OVERLAY_ITEMS:
            self.ttk.Checkbutton(row, text=ITEM_LABELS[item], variable=self.item_vars[item]).pack(side='left')
        self.note(f, 'No jogo, Insert (ou R3+L2 no controle) abre o menu do PC e F10 liga e desliga o '
                     'contador de FPS. O jogo regrava game.ini quando o menu fecha; o launcher relê o '
                     f'arquivo ao abrir e quando o jogo termina.\n{GAME_INI}')
        self.section(f, 'Ferramentas')
        for name, text, binary, hint in (
                ('mangohud', 'MangoHud', 'mangohud', 'sobreposição com FPS, tempos de quadro e temperaturas'),
                ('gamemode', 'GameMode', 'gamemoderun', 'pede ao sistema o modo de desempenho')):
            found = tool_available(binary)
            self.check(f, f'{text} — {hint}' + ('' if found else ' (não instalado)'), self.var(name), found)

    def apply_ini(self):
        """Copy :attr:`ini` into the "Gráficos" widgets.

        A custom window size not among the presets is added to the size
        options (and to the combobox once it exists). Overlay items the
        launcher does not know are remembered in :attr:`extra_items` so
        :meth:`collect_ini` writes them back unchanged.
        """
        for key, var in self.ini_vars.items():
            var.set(self.ini[key])
        if self.ini['window_size'] not in self.size_options:
            self.size_options[self.ini['window_size']] = self.ini['window_size'].replace('x', ' × ')
            if hasattr(self, 'size_box'):
                self.size_box.configure(values=list(self.size_options.values()))
        for item, var in self.item_vars.items():
            var.set(item in self.ini['overlay_items'])
        self.extra_items = [i for i in self.ini['overlay_items'] if i not in game_ini.OVERLAY_ITEMS]

    def reload_ini(self):
        """Re-read ``game.ini`` (the game may have rewritten it) and refresh the widgets."""
        self.ini = game_ini.read(GAME_INI)
        self.apply_ini()

    def build_controls(self):
        """Create the "Controles" page with the binding table.

        Each row of ``CONTROLS`` gets two entries (:attr:`key_vars`, up to two
        keys or mouse buttons) and, when the button can be remapped on the
        pad, a combobox of controller buttons (:attr:`pad_boxes`, value kept
        in :attr:`pad_vars`). Entries are wired to the click-to-capture
        handlers, and every edit re-runs :meth:`check_duplicates`, whose
        warnings appear in the label below the table.
        """
        f =self.page('Controles', 'Teclado, mouse e controle compartilham o jogador 1. Alterações valem ao '
                                   'reiniciar o jogo.')
        self.choice(f, 'Nomes no controle', self.var('pad_style'),
                    {'playstation': 'PlayStation', 'xbox': 'Xbox'}, self.relabel)
        for name, text in (('keyboard', 'Habilitar teclado e mouse'), ('rumble', 'Vibração do controle')):
            self.check(f, text, self.var(name))
        self.note(f, 'WASD: analógico esquerdo • Setas: analógico direito • 1/2/3/4: direcional '
                     '(cima/direita/baixo/esquerda)\nSegurando Esquiva (Alt esquerdo), WASD vira o analógico '
                     'direito. Cada botão aceita até duas teclas ou botões do mouse.')
        self.capture = self.tk.BooleanVar(value=True)
        self.check(f, 'Capturar ao clicar: clique num campo e aperte a tecla ou o botão do mouse '
                      '(Backspace limpa)', self.capture)
        table = self.ttk.Frame(f)
        table.pack(fill='x', pady=8)
        for col, title in enumerate(('BOTÃO DO JOGO', 'TECLA 1', 'TECLA 2', 'CONTROLE')):
            self.ttk.Label(table, text=title, foreground=ACCENT_HI).grid(row=0, column=col, sticky='w', pady=8)
        for row, (name, title, key, pad) in enumerate(CONTROLS, 1):
            self.ttk.Label(table, text=title, width=26 if name == 'evade_mod' else 19,
                           wraplength=220).grid(row=row, column=0, sticky='w', pady=4)
            parts = (self.app['controls']['key'].get(name, key).split('|') + [''])[:2]
            pair = []
            for col, value in enumerate(parts, 1):
                kv = self.tk.StringVar(value=value)
                entry = self.ttk.Entry(table, textvariable=kv, width=15)
                entry.grid(row=row, column=col, padx=(0, 12), pady=4)
                entry.bind('<ButtonPress>', lambda e, v=kv, n=title: self.capture_click(e, v, n))
                entry.bind('<KeyPress>', lambda e, v=kv: self.capture_key(e, v))
                entry.bind('<FocusOut>', lambda e: self.end_capture(e.widget))
                kv.trace_add('write', lambda *_: self.check_duplicates())
                pair.append(kv)
            self.key_vars[name] = tuple(pair)
            if pad:
                self.pad_vars[name] = self.app['controls']['pad'].get(name, pad)
                box = self.ttk.Combobox(table, state='readonly', width=20)
                box.grid(row=row, column=3, pady=4)
                box.bind('<<ComboboxSelected>>', lambda e, n=name, b=box: self.select_pad(n, b.get()))
                self.pad_boxes[name] = box
            else:
                text = f'Gatilho {name.upper()}' if name in ('l2', 'r2') else '—'
                self.ttk.Label(table, text=text).grid(row=row, column=3, sticky='w')
        self.relabel()
        self.warning = self.tk.Label(f, text='', bg=PANEL, fg=ACCENT_HI, justify='left', anchor='w')
        self.warning.pack(fill='x', pady=(0, 8))
        self.check_duplicates()
        self.note(f, 'Nomes de tecla do SDL: letras, números, Space, Return, Tab, Escape, Left Shift, Left Ctrl, '
                     'Left Alt, F1–F12… Mouse: Mouse Left, Mouse Right, Mouse Middle, Mouse X1, Mouse X2.\n'
                     f'{", ".join(RESERVED_KEYS)} são reservadas: Insert abre o menu do jogo, F10 liga o '
                     'contador de FPS e F9 marca o início e o fim de uma cena para medir.')
        self.ttk.Button(f, text='Restaurar controles padrão', command=self.reset_controls).pack(anchor='w')

    def capture_click(self, event, var, title):
        """Handle a mouse press on a binding entry while capture mode is on.

        The first click arms the entry (highlighted style, keyboard focus and
        a prompt in the status bar), disarming any other armed entry. A
        further click on the armed entry binds that mouse button to ``var``
        if it is supported. With capture mode off the event is left to Tk so
        the entry can be edited as text.

        Returns:
            ``'break'`` to stop Tk's default handling, or ``None``.
        """
        if not self.capture.get():
            return None
        if self.capturing is event.widget:
            name = sdl_mouse_name(event.num)
            if name:
                var.set(name)
                self.end_capture(event.widget)
            return 'break'
        if self.capturing is not None:
            self.end_capture(self.capturing)
        self.capturing = event.widget
        event.widget.configure(style='Capture.TEntry')
        event.widget.focus_set()
        self.status.set(f'{title}: aperte uma tecla ou um botão do mouse')
        return 'break'

    def capture_key(self, event, var):
        """Handle a key press on the armed binding entry.

        Backspace clears the binding; a key with an SDL name that is not
        reserved is stored in ``var``. Both end the capture. Unsupported or
        reserved keys only show a message and keep the entry armed. Key
        presses on an entry that is not armed are left to Tk.

        Returns:
            ``'break'`` while capturing, otherwise ``None``.
        """
        if self.capturing is not event.widget:
            return None
        if event.keysym == 'BackSpace':
            var.set('')
            self.end_capture(event.widget)
            return 'break'
        name = sdl_key_name(event.keysym)
        if name is None:
            self.status.set(f'Tecla não suportada: {event.keysym}')
        elif name in RESERVED_KEYS:
            self.status.set(f'{name} está reservada')
        else:
            var.set(name)
            self.end_capture(event.widget)
        return 'break'

    def end_capture(self, widget):
        """Disarm ``widget`` if it is the entry being captured and reset the status bar."""
        if self.capturing is widget:
            self.capturing = None
            widget.configure(style='TEntry')
            self.status.set('Pronto')

    def current_keys(self):
        """Return the keyboard map from the entries as ``{button id: 'Key1|Key2'}``, omitting empty rows."""
        keys = {}
        for name, pair in self.key_vars.items():
            parts = [v.get().strip() for v in pair if v.get().strip()]
            if parts:
                keys[name] = '|'.join(parts)
        return keys

    def check_duplicates(self):
        """Show warnings for keys bound twice or also used by an analog stick.

        Only informational: saving is not blocked. Does nothing while the
        page is still being built and the warning label does not exist yet.
        """
        if not hasattr(self, 'warning'):
            return
        titles = {name: title for name, title, _, _ in CONTROLS}
        lines = [f'Tecla repetida: {key} em ' + ' e '.join(titles[n] for n in names)
                 for key, names in duplicate_bindings(self.current_keys()).items()]
        lines += [f'{key} também move um analógico ({titles[name]})'
                  for name, key in stick_conflicts(self.current_keys()).items()]
        self.warning.configure(text='\n'.join(lines))

    def select_pad(self, name, title):
        """Store the controller button whose label (in the current pad style) is ``title`` for button ``name``."""
        self.pad_vars[name] = next(k for k, v in PAD_NAMES[self.var('pad_style').get()].items() if v == title)

    def relabel(self):
        """Refresh the controller comboboxes with the button names of the selected pad style."""
        labels = PAD_NAMES[self.var('pad_style').get()]
        for name, box in self.pad_boxes.items():
            box.configure(values=list(labels.values()))
            box.set(labels[self.pad_vars[name]])

    def reset_controls(self):
        """Put the default keyboard and controller bindings back in the table (saved only on "Salvar")."""
        for name, _, key, pad in CONTROLS:
            parts = (key.split('|') + [''])[:2]
            for var, value in zip(self.key_vars[name], parts):
                var.set(value)
            if pad:
                self.pad_vars[name] = pad
        self.relabel()

    def build_advanced(self):
        """Create the "Avançado" page: stick dead zone and the configuration file paths."""
        f =self.page('Avançado', 'Ajustes de sensibilidade.')
        self.spin(f, 'Zona morta dos analógicos', self.var('stick_deadzone'), 0, 30000, 500)
        self.note(f, f'0 = máxima sensibilidade; 7849 = padrão.\n\nConfiguração: {CONFIG_FILE}\n'
                     f'Opções do jogo: {GAME_INI}')

    def build_diagnostics(self):
        """Create the "Diagnóstico" page for the ``diagnostics`` settings.

        Creates one Tk variable per ``DIAGNOSTICS`` key in :attr:`diag_vars`
        and adds the measurement, texture, shader (including "Limpar cache de
        shaders"), SPURS and MLAA pacing controls. The parallel workloads
        combobox and the free-text entry are kept in sync by
        :meth:`select_parallel` and :meth:`sync_parallel`.
        """
        f =self.page('Diagnóstico', 'Opções para medir e isolar problemas. Cada uma só vira variável de '
                                     'ambiente quando difere do padrão; o padrão é o de scripts/run.sh.')
        diagnostics = self.app['diagnostics']
        for key, value in DIAGNOSTICS.items():
            cls = self.tk.BooleanVar if isinstance(value, bool) else self.tk.StringVar
            self.diag_vars[key] = cls(value=diagnostics[key])
        self.section(f, 'Medição')
        self.check(f, 'Gravar tempos de quadro em CSV (RSX_FRAME_CSV)', self.diag_vars['frame_csv'])
        self.note(f, f'Gravado na pasta de log de cada execução; {GAME_INI.parent / "frames.csv"} aponta para o mais recente. F9 no jogo grava marks.csv ao lado.')
        self.check(f, 'Medir a decodificação de vídeo (VDEC_PERF)', self.diag_vars['vdec_perf'])
        self.section(f, 'Texturas')
        self.check(f, 'Conversão de texturas na GPU (RSX_VK_TEXTURE_COMPUTE)', self.diag_vars['texture_compute'])
        self.check(f, 'Texturas ARGB sem conversão (RSX_VK_RAW_TEXTURES)', self.diag_vars['raw_textures'])
        self.check(f, 'Hash completo das texturas, mais lento (RSX_TEX_HASH_FULL)', self.diag_vars['tex_hash_full'])
        self.section(f, 'Shaders')
        self.check(f, 'Cache de shaders em disco (GOWA_SHADER_CACHE)', self.diag_vars['shader_cache'])
        self.check(f, 'Compilar shaders em segundo plano (GOWA_ASYNC_SHADERS)', self.diag_vars['async_shaders'])
        self.check(f, 'Pré-compilar os shaders já vistos ao abrir (GOWA_SHADER_WARMUP)',
                   self.diag_vars['shader_warmup'])
        self.note(f, f'Cache: {SHADER_CACHE_DIR}. Em segundo plano, um objeto pode faltar por um quadro '
                     'enquanto o shader dele compila.')
        self.ttk.Button(f, text='Limpar cache de shaders', command=self.clear_shaders).pack(anchor='w', pady=(0, 6))
        self.section(f, 'SPURS')
        self.spin(f, 'Threads do kernel', self.diag_vars['kern_threads'], 1, 8, 1)
        row = self.ttk.Frame(f)
        row.pack(fill='x', pady=6)
        self.ttk.Label(row, text='Workloads em paralelo', width=30).pack(side='left')
        self.parallel_box = self.ttk.Combobox(row, state='readonly', width=24,
                                              values=list(PARALLEL_LABELS.values()))
        self.parallel_box.pack(side='left', padx=(0, 8))
        self.parallel_box.bind('<<ComboboxSelected>>', lambda e: self.select_parallel())
        self.ttk.Entry(row, textvariable=self.diag_vars['kern_parallel'], width=18).pack(side='left')
        self.diag_vars['kern_parallel'].trace_add('write', lambda *_: self.sync_parallel())
        self.sync_parallel()
        self.note(f, 'SPURS_KERN_THREADS: threads do kernel SPURS (1 a 8, padrão 6).\n'
                     'SPURS_KERN_PARALLEL: workloads que rodam em paralelo, separados por vírgula. '
                     'Vazio deixa tudo em série, mais lento e mais seguro.')
        self.section(f, 'Ritmo')
        self.choice(f, 'Ritmo na partida do MLAA', self.diag_vars['flip_min_ms'], FLIP_LABELS)
        self.note(f, 'GCM_FLIP_MIN_MS vale só para os 3 primeiros quadros depois que o antialias nas SPUs '
                     'começa; o resto do jogo não tem atraso artificial. Não é um limitador de FPS.')
        self.ttk.Button(f, text='Restaurar padrões', command=self.reset_diagnostics).pack(anchor='w')

    def select_parallel(self):
        """Copy the picked parallel-workload preset into the entry; "Personalizado" leaves it as is."""
        chosen = next(k for k, v in PARALLEL_LABELS.items() if v == self.parallel_box.get())
        if chosen in PARALLEL_PRESETS:
            self.diag_vars['kern_parallel'].set(PARALLEL_PRESETS[chosen])

    def sync_parallel(self):
        """Select the preset matching the entry text (spaces ignored), or "Personalizado"."""
        value = self.diag_vars['kern_parallel'].get().replace(' ', '')
        preset = next((k for k, v in PARALLEL_PRESETS.items() if v == value), 'custom')
        self.parallel_box.set(PARALLEL_LABELS[preset])

    def clear_shaders(self):
        """Delete the shader cache after the user's click, refusing while the game runs.

        The outcome (refusal, error or number of files removed) is shown in a message box.
        """
        if self.process:
            self.messagebox.showwarning('Shaders', 'Feche o jogo antes de limpar o cache de shaders.')
            return
        try:
            removed = clear_shader_cache()
        except OSError as e:
            self.messagebox.showerror('Shaders', str(e))
            return
        self.messagebox.showinfo('Shaders', f'Cache de shaders limpo ({removed} arquivos).')

    def reset_diagnostics(self):
        """Reset every diagnostic widget to its default (saved only on "Salvar")."""
        for key, value in DIAGNOSTICS.items():
            self.diag_vars[key].set(value)

    def build_log(self):
        """Create the "Log" page: buttons, the event count summary and a read-only text view.

        The text widget has one color tag per classification level
        (``error``, ``gap``, ``warning``) used by :meth:`append_log`.
        """
        f =self.page('Log', f'Saída da execução atual (últimas {LOG_LIMIT} linhas). O arquivo completo é '
                             f'{CONFIG_DIR / "last_run.log"}. Histórico e diagnósticos em logs/.')
        row = self.ttk.Frame(f)
        row.pack(fill='x', pady=(0, 8))
        self.ttk.Button(row, text='Copiar trecho', command=self.copy_log).pack(side='left', padx=(0, 6))
        self.ttk.Button(row, text='Limpar tela', command=self.clear_log).pack(side='left', padx=6)
        self.ttk.Button(row, text='Abrir pasta', command=lambda: self.open_folder(CONFIG_DIR)).pack(side='left', padx=6)
        self.log_summary = self.tk.StringVar(value='Ocorrências classificadas: nenhuma execução')
        self.ttk.Label(f, textvariable=self.log_summary).pack(anchor='w', pady=(0, 6))
        self.log = self.tk.Text(f, bg=BG, fg=TEXT, insertbackground=TEXT, wrap='word',
                                height=23, font=('DejaVu Sans Mono', 9), state='disabled')
        self.log.pack(fill='both', expand=True)
        for level, color in (('error', '#ff7777'), ('gap', '#ffb454'), ('warning', '#e6d28a')):
            self.log.tag_configure(level, foreground=color)

    def append_log(self, text):
        """Append ``text`` to the log view, coloring each line by its classification.

        Lines beyond ``LOG_LIMIT`` are dropped from the top and the view
        scrolls to the end.
        """
        self.log.configure(state='normal')
        for line in text.splitlines(keepends=True):
            hit = classify(line)
            self.log.insert('end', line, (hit[0],) if hit else ())
        lines = int(self.log.index('end-1c').split('.')[0])
        if lines > LOG_LIMIT:
            self.log.delete('1.0', f'{lines - LOG_LIMIT + 1}.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def copy_log(self):
        """Copy the visible log text to the clipboard."""
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get('1.0', 'end-1c'))
        self.status.set('Log copiado')

    def clear_log(self):
        """Empty the log view; the log files on disk are not touched."""
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def open_folder(self, path):
        """Open ``path`` in the file manager (see :func:`open_path`), showing errors in a message box."""
        try:
            open_path(path)
        except OSError as e:
            self.messagebox.showerror('Abrir pasta', str(e))

    def refresh_backups(self):
        """Reload the backup list, select the newest and enable "Restaurar" only if one exists."""
        self.backup_names = list_backups(BACKUP_DIR)
        self.backup_box.configure(values=[backup_label(n) for n in self.backup_names])
        self.backup_box.set(backup_label(self.backup_names[0]) if self.backup_names else 'nenhum backup ainda')
        self.restore_button.configure(state='normal' if self.backup_names else 'disabled')

    def restore_saves(self):
        """Restore the backup selected in the combobox after asking for confirmation.

        Refused while the game runs. The current saves get a safety backup
        first (see :func:`save_backup.restore`); errors are shown in a
        message box and the outcome appears in the status bar.

        Returns:
            ``True`` if the saves were restored, ``False`` otherwise.
        """
        if self.process:
            self.messagebox.showwarning('Saves', 'Feche o jogo antes de restaurar um backup.')
            return False
        index = self.backup_box.current()
        if index < 0:
            return False
        name = self.backup_names[index]
        if not self.messagebox.askyesno('Restaurar saves', f'Substituir os saves atuais pelo backup de '
                                        f'{backup_label(name)}?\nOs saves atuais ganham um backup antes.'):
            return False
        try:
            safety = restore(SAVEDATA_DIR, BACKUP_DIR, name)
        except (OSError, ValueError) as e:
            self.messagebox.showerror('Restaurar saves', str(e))
            return False
        self.refresh_backups()
        self.status.set(f'Saves restaurados de {backup_label(name)}' +
                        (f' • anteriores em {safety.name}' if safety else ''))
        return True

    def show(self, name):
        """Display page ``name``, hide the others and highlight its sidebar button."""
        for key, page in self.pages.items():
            page.pack_forget()
            self.nav[key].configure(bg=ACCENT if key == name else BG)
        self.pages[name].pack(fill='both', expand=True)

    def collect(self):
        """Build a settings dict from the current widget values (not yet validated).

        Starts from a copy of :attr:`app`, applies the top-level variables,
        converts the numeric fields to ``int`` and gathers the diagnostics and
        both control maps.

        Raises:
            ValueError: If the dead zone, kernel threads or MLAA pacing is not an integer.
        """
        data =copy.deepcopy(self.app)
        data.update({k: v.get() for k, v in self.vars.items()})
        try:
            data['stick_deadzone'] = int(data['stick_deadzone'])
            diagnostics = {k: v.get() for k, v in self.diag_vars.items()}
            for key in ('kern_threads', 'flip_min_ms'):
                diagnostics[key] = int(diagnostics[key])
        except ValueError:
            raise ValueError('Zona morta, threads do kernel e ritmo do MLAA precisam ser números inteiros.')
        data['diagnostics'] = diagnostics
        data['controls'] = {'key': self.current_keys(), 'pad': dict(self.pad_vars)}
        return data

    def collect_ini(self):
        """Return the validated ``game.ini`` options from the "Gráficos" widgets.

        Overlay items are listed in ``OVERLAY_ITEMS`` order followed by any
        unknown items read from the file.

        Raises:
            ValueError: If a value is invalid.
        """
        values ={key: var.get() for key, var in self.ini_vars.items()}
        values['overlay_items'] = [i for i in game_ini.OVERLAY_ITEMS if self.item_vars[i].get()] + self.extra_items
        return game_ini.validate({**self.ini, **values})

    def save(self):
        """Validate and write ``settings.json`` and ``game.ini`` from the widgets.

        On success the in-memory copies are updated and the status bar says
        the changes apply on the next run; on failure the error is shown in a
        message box. ``settings.json`` is written before ``game.ini``, so an
        error writing the latter can leave only the former updated.

        Returns:
            ``True`` if both files were saved.
        """
        try:
            data = self.collect()
            ini = self.collect_ini()
            save(data)
            game_ini.write(GAME_INI, ini)
            self.app, self.ini = data, ini
            self.status.set('Salvo • aplica na próxima execução')
            return True
        except (OSError, ValueError) as e:
            self.messagebox.showerror('Configuração', str(e))
            return False

    def play(self):
        """Save the settings, back up the saves and start the game.

        Does nothing if a game is already running or saving fails. The game
        is started by :class:`run_log.RunLog`, and its ``session.log`` is
        opened for reading so :meth:`tick` can follow it. If starting fails,
        a process that was already spawned is terminated, whatever reached
        ``last_run.log`` is shown in the Log page and an error box appears.
        On success the run counter is incremented, the log view is reset and
        the "JOGAR"/"Parar" buttons are toggled.
        """
        if self.process or not self.save():
            return
        log_lines = []
        try:
            env = game_environment(self.app, ini_path=GAME_INI)
            log_lines.append(backup_saves())
            command = game_command(self.app)
            log_lines.append('[launcher] ' + ' '.join(command))
            self.run_log = RunLog(CONFIG_DIR, PORT, command, env, log_lines)
            self.process = self.run_log.process
            self.log_reader = open(self.run_log.directory / 'session.log', 'r', encoding='utf-8', errors='replace')
        except (OSError, ValueError) as e:
            if self.run_log:
                if self.run_log.process and self.run_log.process.poll() is None:
                    os.killpg(self.run_log.process.pid, signal.SIGTERM)
                    self.run_log.process.wait(timeout=5)
                self.run_log.close()
                self.run_log = None
                self.process = None
            if self.log_reader:
                self.log_reader.close()
                self.log_reader = None
            self.clear_log()
            try:
                with (CONFIG_DIR / 'last_run.log').open(encoding='utf-8', errors='replace') as failed_log:
                    self.append_log(failed_log.read(65536))
            except OSError:
                pass
            self.append_log(f'[launcher:error] Não foi possível iniciar: {e}\n')
            self.messagebox.showerror('Não foi possível iniciar', str(e))
            return
        self.generation += 1
        self.logging_error_seen = False
        self.partial = ''
        self.clear_log()
        self.follow(self.log_reader.read(65536))
        self.refresh_backups()
        self.play_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.status.set('Jogo em execução')

    def stop(self):
        """Ask the running game to quit.

        Marks the stop as requested in the run log (so the exit is not
        reported as a failure) and sends ``SIGTERM`` to the game's process
        group. If the same run is still alive 2 seconds later, ``SIGKILL`` is
        sent. The UI is reset by :meth:`tick` once the process has exited.
        """
        process, generation = self.process, self.generation
        if not process or process.poll() is not None:
            return
        try:
            self.run_log.stop_requested = True
            self.run_log.note('stop', f'SIGTERM requested pid={process.pid}')
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        self.status.set('Encerrando…')

        def force():
            """Kill the process group if the run that was stopped is still alive."""
            if generation == self.generation and process.poll() is None:
                try:
                    self.run_log.note('stop', f'SIGKILL escalation pid={process.pid}')
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.root.after(2000, force)

    def follow(self, chunk):
        """Show a chunk of ``session.log`` and update the status bar from milestone lines.

        Complete lines are checked with :func:`status_for_line`; the trailing
        partial line is kept (at most its last 4096 characters) for the next chunk.
        """
        self.append_log(chunk)
        lines = (self.partial + chunk).split('\n')
        self.partial = lines.pop()[-4096:]
        for line in lines:
            status = status_for_line(line)
            if status:
                self.status.set(status)

    def tick(self):
        """Poll the game and its log; reschedules itself every 100 ms.

        Pumps the :class:`run_log.RunLog`, reports a logging failure once,
        updates the event count summary and shows up to 64 KiB of new
        ``session.log`` text. When the run has finished and the log has been
        read to the end, the UI is reset, ``game.ini`` is re-read and the
        exit code appears in the status bar. If the window is closing and no
        game is running, the window is destroyed instead of rescheduling.
        """
        if self.run_log and not self.run_log.finished:
            self.run_log.pump()
            if self.run_log.logging_error and not self.logging_error_seen:
                self.logging_error_seen = True
                self.append_log(f'[launcher:error] Falha ao gravar diagnóstico: {self.run_log.logging_error}. Log estruturado incompleto; confira espaço em disco e raw.log.\n')
            self.log_summary.set('Ocorrências emitidas: ' + (', '.join(f'{key}={value}' for key, value in self.run_log.counts.items()) or 'nenhuma classificada'))
        if self.log_reader:
            chunk = self.log_reader.read(65536)
            if chunk:
                self.follow(chunk)
        if self.process and self.run_log.finished and len(chunk) < 65536:
            code = self.process.returncode
            self.process = None
            self.log_reader.close()
            self.log_reader = None
            self.play_button.configure(state='normal')
            self.stop_button.configure(state='disabled')
            self.reload_ini()
            self.status.set(f'Jogo encerrado ({code})')
        if self.closing and not self.process:
            self.destroy()
            return
        self.root.after(100, self.tick)

    def close(self):
        """Handle the window close button.

        Saves the settings first and stays open if that fails. Otherwise the
        window is destroyed right away, or, if a game is running, the game
        is stopped and :meth:`tick` destroys the window after it exits.
        """
        if not self.save():
            return
        self.closing = True
        if self.process:
            self.stop()
        else:
            self.destroy()

    def destroy(self):
        """Cancel every pending Tk ``after`` callback and destroy the root window."""
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        self.root.destroy()


def main():
    """Command-line entry point.

    ``--play`` loads the saved settings, backs up the saves and replaces the
    current process with the game command (``os.execvpe`` with the working
    directory set to the port root). Without it the Tk launcher runs. The
    hidden ``--exit-after MS`` option shows every page once and closes the
    window after ``MS`` milliseconds, for automated smoke tests.

    Returns:
        Process exit status: 0 on normal exit, 1 if setup failed (the error
        is printed to stderr).
    """
    parser =argparse.ArgumentParser(description='God of War: Ascension — PC')
    parser.add_argument('--play', action='store_true', help='Iniciar com a configuração salva, sem launcher')
    parser.add_argument('--exit-after', type=int, metavar='MS', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.play:
            data = load()
            env = game_environment(data)
            print(backup_saves(), file=sys.stderr)
            command = game_command(data)
            os.chdir(PORT)
            os.execvpe(command[0], command, env)
        import tkinter as tk
        root = tk.Tk()
        app = Launcher(root)
        if args.exit_after is not None:
            for name in PAGES:
                app.show(name)
                root.update()
            root.after(args.exit_after, app.destroy)
        root.mainloop()
    except (OSError, ValueError, ImportError) as e:
        print(f'Launcher: {e}\nNo Linux, a interface requer Python, Tk e SDL2.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
