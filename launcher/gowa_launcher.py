#!/usr/bin/env python3
"""Ascension PC launcher. Layout/palette reference: gow3_pc's Tk launcher.

The settings model is separate so a future in-game overlay can use the same
schema. Unsupported PS4 graphics features are intentionally not exposed.
"""
import argparse
import copy
import os
import signal
import subprocess
import sys
from pathlib import Path
from settings import (PORT, CONFIG_FILE, CONFIG_DIR, CONTROLS, PAD_NAMES, DEFAULTS,
                      load, save, game_environment, game_command)

BG, PANEL, CARD, LINE = '#0d0b0b', '#161212', '#1d1717', '#2a2222'
TEXT, MUTED, ACCENT, ACCENT_HI = '#e6e0d8', '#8a817a', '#a4161a', '#c81e25'


class Launcher:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk, messagebox
        self.tk, self.ttk, self.messagebox, self.root = tk, ttk, messagebox, root
        self.process = self.log_reader = self.log_writer = None
        self.closing = False
        self.generation = 0
        self.app = load()
        self.vars, self.key_vars, self.pad_vars, self.pad_boxes = {}, {}, {}, {}
        root.title('God of War: Ascension (PC)')
        root.configure(bg=BG)
        root.geometry('1120x740')
        root.minsize(860, 600)
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', background=PANEL, foreground=TEXT, fieldbackground=CARD,
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, font=('DejaVu Sans', 10))
        style.configure('TButton', background=CARD, padding=(12, 7))
        style.map('TButton', background=[('active', ACCENT)], foreground=[('disabled', MUTED)])
        style.configure('Play.TButton', background=ACCENT, font=('DejaVu Sans', 12, 'bold'))
        style.configure('TEntry', insertcolor=TEXT, padding=5)
        style.configure('TCombobox', padding=5, arrowcolor=TEXT)
        style.map('TCombobox', fieldbackground=[('readonly', CARD)], foreground=[('readonly', TEXT)],
                  selectbackground=[('readonly', CARD)], selectforeground=[('readonly', TEXT)])
        style.configure('TCheckbutton', padding=6)
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
        for name in ('Início', 'Jogo', 'Gráficos', 'Controles', 'Avançado', 'Log'):
            button = tk.Button(side, text=name, anchor='w', bd=0, highlightthickness=0, padx=24, pady=12,
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

    def var(self, name):
        if name not in self.vars:
            cls = self.tk.BooleanVar if isinstance(self.app[name], bool) else self.tk.StringVar
            self.vars[name] = cls(value=self.app[name])
        return self.vars[name]

    def choice(self, frame, title, name, options):
        row = self.ttk.Frame(frame)
        row.pack(fill='x', pady=8)
        self.ttk.Label(row, text=title, width=24).pack(side='left')
        var = self.var(name)
        label = self.tk.StringVar(value=options.get(str(var.get()), str(var.get())))
        box = self.ttk.Combobox(row, values=list(options.values()), state='readonly', width=31,
                               textvariable=label)
        var.trace_add('write', lambda *_: label.set(options.get(str(var.get()), str(var.get()))))
        box.bind('<<ComboboxSelected>>', lambda e: var.set(next(k for k, v in options.items() if v == box.get())))
        box.pack(side='left')
        return box

    def build_pages(self):
        f = self.page('Início', 'O mesmo padrão de configuração dos ports God of War no PC.')
        self.tk.Label(f, text='GOD OF WAR\nASCENSION', bg=CARD, fg=TEXT,
                      font=('DejaVu Serif', 32), pady=45).pack(fill='x', pady=20)
        self.note(f, 'Continue a jornada de Kratos. Configure seus controles antes de jogar.\n\n'
                     'Port em desenvolvimento: a campanha completa ainda não foi validada.')
        f = self.page('Jogo', 'Instalação preparada neste workspace.')
        for label, path in (('Executável', PORT / 'build/linux-vulkan/GowAscensionRecomp'),
                            ('Jogo', PORT / 'game/EBOOT.ELF'), ('Disco', PORT / 'game/disc/PS3_GAME'),
                            ('Atualização', PORT / 'game/update')):
            self.note(f, f'{label}: {"encontrado" if path.exists() else "ausente"}\n{path}')
        self.note(f, 'Esta versão usa a instalação já extraída. O assistente de importação será adicionado depois.')
        f = self.page('Gráficos', 'As alterações são aplicadas na próxima execução.')
        self.choice(f, 'Modo de exibição', 'display_mode', {'windowed': 'Janela', 'borderless': 'Tela cheia sem bordas'})
        self.note(f, 'Resolução do jogo: 1280 × 720. A tela cheia ajusta a apresentação ao monitor.\n'
                     'Outras resoluções, upscaling e opções de efeitos ainda não estão disponíveis.')
        f = self.page('Controles', 'Teclado e controle compartilham o jogador 1. Alterações valem ao reiniciar o jogo.')
        style = self.choice(f, 'Nomes no controle', 'pad_style', {'playstation': 'PlayStation', 'xbox': 'Xbox'})
        style.bind('<<ComboboxSelected>>', lambda e: (self.var('pad_style').set(
            'xbox' if style.get() == 'Xbox' else 'playstation'), self.relabel()))
        for name, label in (('keyboard', 'Habilitar teclado'), ('rumble', 'Vibração do controle')):
            self.ttk.Checkbutton(f, text=label, variable=self.var(name)).pack(anchor='w')
        self.note(f, 'WASD: analógico esquerdo • Setas: analógico direito • IJKL: direcional\n'
                     'Espaço confirma como Cross. Enter corresponde a Start; Tab a Select.')
        table = self.ttk.Frame(f)
        table.pack(fill='x', pady=8)
        for col, title in enumerate(('BOTÃO DO JOGO', 'TECLADO', 'CONTROLE')):
            self.ttk.Label(table, text=title, foreground=ACCENT_HI).grid(row=0, column=col, sticky='w', pady=8)
        for row, (name, label, key, pad) in enumerate(CONTROLS, 1):
            self.ttk.Label(table, text=label, width=19).grid(row=row, column=0, sticky='w', pady=4)
            kv = self.tk.StringVar(value=self.app['controls']['key'].get(name, key))
            self.key_vars[name] = kv
            self.ttk.Entry(table, textvariable=kv, width=17).grid(row=row, column=1, padx=(0, 18), pady=4)
            if pad:
                self.pad_vars[name] = self.app['controls']['pad'].get(name, pad)
                box = self.ttk.Combobox(table, state='readonly', width=22)
                box.grid(row=row, column=2, pady=4)
                box.bind('<<ComboboxSelected>>', lambda e, n=name, b=box: self.select_pad(n, b.get()))
                self.pad_boxes[name] = box
            else:
                self.ttk.Label(table, text=f'Gatilho {name.upper()}').grid(row=row, column=2, sticky='w')
        self.relabel()
        self.note(f, 'Teclas: letras, números, Space, Return, Tab, Left Shift, Left Ctrl, Escape, F1–F12.\n'
                     'Insert está reservado para um futuro menu PC. Esse menu ainda não está disponível.')
        self.ttk.Button(f, text='Restaurar controles padrão', command=self.reset_controls).pack(anchor='w')
        f = self.page('Avançado', 'Ajustes de compatibilidade e sensibilidade.')
        self.choice(f, 'Ritmo na partida do MLAA', 'flip_min_ms',
                    {'250': 'Padrão', '500': 'Conservador', '1500': 'Muito conservador',
                     '0': 'Desligado (pode travar)'})
        self.note(f, 'Vale só para os 3 primeiros quadros depois que o antialias nas SPUs começa; '
                     'o resto do jogo não tem atraso artificial. Não é um limitador de FPS.')
        row = self.ttk.Frame(f)
        row.pack(fill='x', pady=8)
        self.ttk.Label(row, text='Zona morta dos analógicos', width=28).pack(side='left')
        self.ttk.Spinbox(row, from_=0, to=30000, increment=500,
                         textvariable=self.var('stick_deadzone'), width=10).pack(side='left')
        self.note(f, f'0 = máxima sensibilidade; 7849 = padrão.\n\nConfiguração: {CONFIG_FILE}')
        f = self.page('Log', 'Saída da execução atual. O arquivo completo fica na pasta de configuração.')
        self.log = self.tk.Text(f, bg=BG, fg=TEXT, insertbackground=TEXT, wrap='word',
                                height=23, font=('DejaVu Sans Mono', 9), state='disabled')
        self.log.pack(fill='both', expand=True)

    def select_pad(self, name, label):
        self.pad_vars[name] = next(k for k, v in PAD_NAMES[self.var('pad_style').get()].items() if v == label)

    def relabel(self):
        labels = PAD_NAMES[self.var('pad_style').get()]
        for name, box in self.pad_boxes.items():
            box.configure(values=list(labels.values()))
            box.set(labels[self.pad_vars[name]])

    def reset_controls(self):
        for name, _, key, pad in CONTROLS:
            self.key_vars[name].set(key)
            if pad:
                self.pad_vars[name] = pad
        self.relabel()

    def show(self, name):
        for key, page in self.pages.items():
            page.pack_forget()
            self.nav[key].configure(bg=ACCENT if key == name else BG)
        self.pages[name].pack(fill='both', expand=True)

    def collect(self):
        data = copy.deepcopy(self.app)
        data.update({k: v.get() for k, v in self.vars.items()})
        for key in ('stick_deadzone', 'flip_min_ms'):
            data[key] = int(data[key])
        data['controls'] = {'key': {k: v.get().strip() for k, v in self.key_vars.items()},
                            'pad': dict(self.pad_vars)}
        return data

    def save(self):
        try:
            data = self.collect()
            save(data)
            self.app = data
            self.status.set('Salvo • aplica na próxima execução')
            return True
        except (OSError, ValueError) as e:
            self.messagebox.showerror('Configuração', str(e))
            return False

    def play(self):
        if self.process or not self.save():
            return
        try:
            env = game_environment(self.app)
            self.log_writer = open(CONFIG_DIR / 'last_run.log', 'wb')
            self.log_reader = open(CONFIG_DIR / 'last_run.log', 'r', encoding='utf-8', errors='replace')
            self.process = subprocess.Popen(game_command(), cwd=PORT, env=env,
                stdout=self.log_writer, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            if self.log_writer:
                self.log_writer.close()
            if self.log_reader:
                self.log_reader.close()
                self.log_reader = None
            self.messagebox.showerror('Não foi possível iniciar', str(e))
            return
        self.generation += 1
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
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

    def tick(self):
        if self.log_reader:
            chunk = self.log_reader.read(65536)
            if chunk:
                self.log.configure(state='normal')
                self.log.insert('end', chunk)
                lines = int(self.log.index('end-1c').split('.')[0])
                if lines > 6000:
                    self.log.delete('1.0', f'{lines - 6000}.0')
                self.log.see('end')
                self.log.configure(state='disabled')
        if self.process and self.process.poll() is not None:
            code = self.process.returncode
            self.process = None
            self.log_writer.close()
            self.log_reader.close()
            self.log_reader = None
            self.play_button.configure(state='normal')
            self.stop_button.configure(state='disabled')
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
    args = parser.parse_args()
    try:
        if args.play:
            env = game_environment(load())
            os.chdir(PORT)
            os.execvpe('bash', game_command(), env)
        import tkinter as tk
        root = tk.Tk()
        Launcher(root)
        root.mainloop()
    except (OSError, ValueError, ImportError) as e:
        print(f'Launcher: {e}\nNo Linux, a interface requer Python, Tk e SDL2.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
