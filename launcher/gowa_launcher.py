#!/usr/bin/env python3
import argparse
import copy
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import game_ini
from keynames import sdl_key_name, sdl_mouse_name
from save_backup import backup, label as backup_label, list_backups, restore
from settings import (PORT, CONFIG_FILE, CONFIG_DIR, GAME_INI, BACKUP_DIR, SAVEDATA_DIR, CONTROLS,
                      PAD_NAMES, DIAGNOSTICS, PARALLEL_PRESETS, RESERVED_KEYS, load, save,
                      game_environment, game_command, tool_available, duplicate_bindings,
                      stick_conflicts, clear_shader_cache, SHADER_CACHE_DIR)

BG, PANEL, CARD, LINE = '#0d0b0b', '#161212', '#1d1717', '#2a2222'
TEXT, MUTED, ACCENT, ACCENT_HI = '#e6e0d8', '#8a817a', '#a4161a', '#c81e25'
PAGES = ('Início', 'Jogo', 'Gráficos', 'Controles', 'Avançado', 'Diagnóstico', 'Log')
LOG_LIMIT = 6000
STATUS_MARKERS = (
    (re.compile(r'^erro: (.*)'), 'Erro ao iniciar: {0}'),
    (re.compile(r'^\[boot\] VFS root'), 'Carregando o jogo…'),
    (re.compile(r'^\[boot\] dispatching entry'), 'Iniciando o jogo…'),
    (re.compile(r'^\[rsx\] .*backend init OK'), 'Jogo aberto'),
    (re.compile(r'^\[rsx\] .*backend init FAILED'), 'Falha ao abrir a janela do jogo'),
)
DISPLAY_LABELS = {'windowed': 'Janela', 'borderless': 'Tela cheia sem bordas', 'fullscreen': 'Tela cheia'}
SIZE_LABELS = {'1280x720': '1280 × 720', '1600x900': '1600 × 900', '1920x1080': '1920 × 1080',
               'monitor': 'Tamanho do monitor'}
CORNER_LABELS = {'tl': 'Superior esquerdo', 'tr': 'Superior direito',
                 'bl': 'Inferior esquerdo', 'br': 'Inferior direito'}
ITEM_LABELS = {'fps': 'FPS', 'frametime': 'Tempo de quadro', 'graph': 'Gráfico',
               'present': 'Apresentação', 'spu': 'SPU', 'shaders': 'Shaders'}
FLIP_LABELS = {'250': 'Padrão', '500': 'Conservador', '1500': 'Muito conservador',
               '0': 'Desligado (pode travar)'}
PARALLEL_LABELS = {'Padrão': 'Padrão (7,13,14,15,16)', 'Seguro': 'Seguro (tudo em série)',
                   'custom': 'Personalizado'}


def status_for_line(line):
    for pattern, text in STATUS_MARKERS:
        match = pattern.search(line)
        if match:
            return text.format(*match.groups())
    return None


def open_path(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    subprocess.Popen(['xdg-open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def backup_saves():
    try:
        made = backup(SAVEDATA_DIR, BACKUP_DIR)
    except OSError as e:
        return f'[launcher] falha no backup dos saves (o jogo abre mesmo assim): {e}'
    if made:
        return f'[launcher] backup dos saves: {made}'
    return '[launcher] sem saves para copiar'


class Launcher:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk, messagebox
        self.tk, self.ttk, self.messagebox, self.root = tk, ttk, messagebox, root
        self.process = self.log_reader = self.log_writer = None
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
        label = self.tk.Label(frame, text=text, bg=PANEL, fg=MUTED, justify='left', anchor='w',
                              wraplength=700, font=('DejaVu Sans', 10))
        label.pack(fill='x', pady=(0, 18))
        label.bind('<Configure>', lambda e: label.configure(wraplength=max(200, e.width - 8)))
        return label

    def section(self, frame, title):
        self.tk.Label(frame, text=title.upper(), bg=PANEL, fg=ACCENT_HI, anchor='w',
                      font=('DejaVu Sans', 10, 'bold')).pack(fill='x', pady=(14, 4))

    def var(self, name):
        if name not in self.vars:
            cls = self.tk.BooleanVar if isinstance(self.app[name], bool) else self.tk.StringVar
            self.vars[name] = cls(value=self.app[name])
        return self.vars[name]

    def choice(self, frame, title, var, options, command=None):
        row = self.ttk.Frame(frame)
        row.pack(fill='x', pady=6)
        self.ttk.Label(row, text=title, width=30).pack(side='left')
        label = self.tk.StringVar(value=options.get(str(var.get()), str(var.get())))
        box = self.ttk.Combobox(row, values=list(options.values()), state='readonly', width=31,
                                textvariable=label)
        var.trace_add('write', lambda *_: label.set(options.get(str(var.get()), str(var.get()))))

        def selected(_event):
            var.set(next(k for k, v in options.items() if v == box.get()))
            if command:
                command()
        box.bind('<<ComboboxSelected>>', selected)
        box.pack(side='left')
        return box

    def spin(self, frame, title, var, low, high, step):
        row = self.ttk.Frame(frame)
        row.pack(fill='x', pady=6)
        self.ttk.Label(row, text=title, width=30).pack(side='left')
        self.ttk.Spinbox(row, from_=low, to=high, increment=step, textvariable=var, width=10).pack(side='left')
        return row

    def check(self, frame, text, var, enabled=True):
        button = self.ttk.Checkbutton(frame, text=text, variable=var, state='normal' if enabled else 'disabled')
        button.pack(anchor='w')
        return button

    def build_pages(self):
        self.build_home()
        self.build_game()
        self.build_graphics()
        self.build_controls()
        self.build_advanced()
        self.build_diagnostics()
        self.build_log()

    def build_home(self):
        f = self.page('Início', 'O mesmo padrão de configuração dos ports God of War no PC.')
        self.tk.Label(f, text='GOD OF WAR\nASCENSION', bg=CARD, fg=TEXT,
                      font=('DejaVu Serif', 32), pady=45).pack(fill='x', pady=20)
        self.note(f, 'Continue a jornada de Kratos. Configure seus controles antes de jogar.\n\n'
                     'No jogo, Insert (ou R3+L2 no controle) abre o menu do PC e F10 liga o contador de FPS.\n\n'
                     'Port em desenvolvimento: a campanha completa ainda não foi validada.')

    def build_game(self):
        f = self.page('Jogo', 'Instalação preparada neste workspace.')
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
        f = self.page('Gráficos', 'Gravado em game.ini, o mesmo arquivo do menu do jogo. Vale na próxima execução.')
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
        self.ini = game_ini.read(GAME_INI)
        self.apply_ini()

    def build_controls(self):
        f = self.page('Controles', 'Teclado, mouse e controle compartilham o jogador 1. Alterações valem ao '
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
                     f'{" e ".join(RESERVED_KEYS)} são reservadas: Insert abre o menu do jogo e F10 liga o '
                     'contador de FPS.')
        self.ttk.Button(f, text='Restaurar controles padrão', command=self.reset_controls).pack(anchor='w')

    def capture_click(self, event, var, title):
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
        if self.capturing is widget:
            self.capturing = None
            widget.configure(style='TEntry')
            self.status.set('Pronto')

    def current_keys(self):
        keys = {}
        for name, pair in self.key_vars.items():
            parts = [v.get().strip() for v in pair if v.get().strip()]
            if parts:
                keys[name] = '|'.join(parts)
        return keys

    def check_duplicates(self):
        if not hasattr(self, 'warning'):
            return
        titles = {name: title for name, title, _, _ in CONTROLS}
        lines = [f'Tecla repetida: {key} em ' + ' e '.join(titles[n] for n in names)
                 for key, names in duplicate_bindings(self.current_keys()).items()]
        lines += [f'{key} também move um analógico ({titles[name]})'
                  for name, key in stick_conflicts(self.current_keys()).items()]
        self.warning.configure(text='\n'.join(lines))

    def select_pad(self, name, title):
        self.pad_vars[name] = next(k for k, v in PAD_NAMES[self.var('pad_style').get()].items() if v == title)

    def relabel(self):
        labels = PAD_NAMES[self.var('pad_style').get()]
        for name, box in self.pad_boxes.items():
            box.configure(values=list(labels.values()))
            box.set(labels[self.pad_vars[name]])

    def reset_controls(self):
        for name, _, key, pad in CONTROLS:
            parts = (key.split('|') + [''])[:2]
            for var, value in zip(self.key_vars[name], parts):
                var.set(value)
            if pad:
                self.pad_vars[name] = pad
        self.relabel()

    def build_advanced(self):
        f = self.page('Avançado', 'Ajustes de sensibilidade.')
        self.spin(f, 'Zona morta dos analógicos', self.var('stick_deadzone'), 0, 30000, 500)
        self.note(f, f'0 = máxima sensibilidade; 7849 = padrão.\n\nConfiguração: {CONFIG_FILE}\n'
                     f'Opções do jogo: {GAME_INI}')

    def build_diagnostics(self):
        f = self.page('Diagnóstico', 'Opções para medir e isolar problemas. Cada uma só vira variável de '
                                     'ambiente quando difere do padrão; o padrão é o de scripts/run.sh.')
        diagnostics = self.app['diagnostics']
        for key, value in DIAGNOSTICS.items():
            cls = self.tk.BooleanVar if isinstance(value, bool) else self.tk.StringVar
            self.diag_vars[key] = cls(value=diagnostics[key])
        self.section(f, 'Medição')
        self.check(f, 'Gravar tempos de quadro em CSV (RSX_FRAME_CSV)', self.diag_vars['frame_csv'])
        self.note(f, f'Arquivo: {GAME_INI.parent / "frames.csv"} (regravado a cada execução)')
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
        chosen = next(k for k, v in PARALLEL_LABELS.items() if v == self.parallel_box.get())
        if chosen in PARALLEL_PRESETS:
            self.diag_vars['kern_parallel'].set(PARALLEL_PRESETS[chosen])

    def sync_parallel(self):
        value = self.diag_vars['kern_parallel'].get().replace(' ', '')
        preset = next((k for k, v in PARALLEL_PRESETS.items() if v == value), 'custom')
        self.parallel_box.set(PARALLEL_LABELS[preset])

    def clear_shaders(self):
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
        for key, value in DIAGNOSTICS.items():
            self.diag_vars[key].set(value)

    def build_log(self):
        f = self.page('Log', f'Saída da execução atual (últimas {LOG_LIMIT} linhas). O arquivo completo é '
                             f'{CONFIG_DIR / "last_run.log"}.')
        row = self.ttk.Frame(f)
        row.pack(fill='x', pady=(0, 8))
        self.ttk.Button(row, text='Copiar', command=self.copy_log).pack(side='left', padx=(0, 6))
        self.ttk.Button(row, text='Limpar', command=self.clear_log).pack(side='left', padx=6)
        self.ttk.Button(row, text='Abrir pasta', command=lambda: self.open_folder(CONFIG_DIR)).pack(side='left', padx=6)
        self.log = self.tk.Text(f, bg=BG, fg=TEXT, insertbackground=TEXT, wrap='word',
                                height=23, font=('DejaVu Sans Mono', 9), state='disabled')
        self.log.pack(fill='both', expand=True)

    def append_log(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        lines = int(self.log.index('end-1c').split('.')[0])
        if lines > LOG_LIMIT:
            self.log.delete('1.0', f'{lines - LOG_LIMIT + 1}.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get('1.0', 'end-1c'))
        self.status.set('Log copiado')

    def clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def open_folder(self, path):
        try:
            open_path(path)
        except OSError as e:
            self.messagebox.showerror('Abrir pasta', str(e))

    def refresh_backups(self):
        self.backup_names = list_backups(BACKUP_DIR)
        self.backup_box.configure(values=[backup_label(n) for n in self.backup_names])
        self.backup_box.set(backup_label(self.backup_names[0]) if self.backup_names else 'nenhum backup ainda')
        self.restore_button.configure(state='normal' if self.backup_names else 'disabled')

    def restore_saves(self):
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
        for key, page in self.pages.items():
            page.pack_forget()
            self.nav[key].configure(bg=ACCENT if key == name else BG)
        self.pages[name].pack(fill='both', expand=True)

    def collect(self):
        data = copy.deepcopy(self.app)
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
        values = {key: var.get() for key, var in self.ini_vars.items()}
        values['overlay_items'] = [i for i in game_ini.OVERLAY_ITEMS if self.item_vars[i].get()] + self.extra_items
        return game_ini.validate({**self.ini, **values})

    def save(self):
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
        if self.process or not self.save():
            return
        log_lines = []
        try:
            env = game_environment(self.app, ini_path=GAME_INI)
            log_lines.append(backup_saves())
            command = game_command(self.app)
            log_lines.append('[launcher] ' + ' '.join(command))
            self.log_writer = open(CONFIG_DIR / 'last_run.log', 'wb')
            self.log_reader = open(CONFIG_DIR / 'last_run.log', 'r', encoding='utf-8', errors='replace')
            self.process = subprocess.Popen(command, cwd=PORT, env=env,
                                            stdout=self.log_writer, stderr=subprocess.STDOUT,
                                            start_new_session=True)
        except (OSError, ValueError) as e:
            if self.log_writer:
                self.log_writer.close()
            if self.log_reader:
                self.log_reader.close()
                self.log_reader = None
            self.messagebox.showerror('Não foi possível iniciar', str(e))
            return
        self.generation += 1
        self.partial = ''
        self.clear_log()
        self.append_log('\n'.join(log_lines) + '\n')
        self.refresh_backups()
        self.play_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.status.set('Jogo em execução')

    def stop(self):
        process, generation = self.process, self.generation
        if not process or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        self.status.set('Encerrando…')

        def force():
            if generation == self.generation and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.root.after(2000, force)

    def follow(self, chunk):
        self.append_log(chunk)
        lines = (self.partial + chunk).split('\n')
        self.partial = lines.pop()[-4096:]
        for line in lines:
            status = status_for_line(line)
            if status:
                self.status.set(status)

    def tick(self):
        if self.log_reader:
            chunk = self.log_reader.read(65536)
            if chunk:
                self.follow(chunk)
        if self.process and self.process.poll() is not None:
            code = self.process.returncode
            self.process = None
            rest = self.log_reader.read()
            if rest:
                self.follow(rest)
            self.log_writer.close()
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
        if not self.save():
            return
        self.closing = True
        if self.process:
            self.stop()
        else:
            self.destroy()

    def destroy(self):
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description='God of War: Ascension — PC')
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
