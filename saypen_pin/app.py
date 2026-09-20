"""Korean Tk desktop application. No network client or telemetry."""
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from . import __version__
from .catalog import bundled_catalogs, load_catalog, CatalogError
from .engine import build_batch, UserError, Cancelled

BG='#f3f5f8'
INK='#192d42'
BLUE='#205bd4'

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'세이펜 PIN 메이커  ·  {__version__}')
        self.geometry('1080x800'); self.minsize(880,700)
        self.configure(bg=BG)
        self.option_add('*Font',('맑은 고딕',10))
        style=ttk.Style(self); style.theme_use('clam')
        style.configure('TFrame',background=BG)
        style.configure('TLabel',background=BG,foreground=INK)
        style.configure('TLabelframe',background=BG)
        style.configure('TLabelframe.Label',background=BG,foreground=INK,font=('맑은 고딕',11,'bold'))
        style.configure('TButton',padding=(12,7),font=('맑은 고딕',10))
        style.configure('Small.TButton',padding=(10,3),font=('맑은 고딕',9))
        style.configure('Primary.TButton',background=BLUE,foreground='white',font=('맑은 고딕',11,'bold'))
        style.map('Primary.TButton',background=[('disabled','#9daecb'),('active','#1747a9')])
        style.configure('Treeview',rowheight=33,font=('맑은 고딕',10))
        style.configure('TCheckbutton',background=BG)
        self.catalogs=bundled_catalogs(); self.catalog=self.catalogs[0]
        self.selected={};self.path_vars={};self.busy=False;self.output=None
        self.events=queue.Queue();self.cancel_event=threading.Event()
        self.template=tk.StringVar();self.folder=tk.StringVar()
        self.rights=tk.BooleanVar();self.status=tk.StringVar(value='책을 고르고, 가지고 있는 MP3를 연결해 주세요.')
        self.count=tk.StringVar();self.catalog_name=tk.StringVar(value=self.catalog.title)
        self.controls=[]
        self._layout();self._populate_books();self._refresh_count()
        self.protocol('WM_DELETE_WINDOW',self._close)
        self.after(100,self._poll)

    def _button(self,parent,text,command,**kw):
        b=ttk.Button(parent,text=text,command=command,**kw);self.controls.append(b);return b

    def _layout(self):
        root=ttk.Frame(self,padding=20);root.pack(fill='both',expand=True)
        ttk.Label(root,text='내 음원으로 만드는 세이펜 파일',font=('맑은 고딕',22,'bold')).pack(anchor='w')
        ttk.Label(root,text='음원은 이 컴퓨터에서만 처리됩니다. 원본은 그대로 두고 새 PIN을 만듭니다.',foreground='#586b7e').pack(anchor='w',pady=(4,10))
        setup=ttk.LabelFrame(root,text='1  준비 파일',padding=12);setup.pack(fill='x')
        setup.columnconfigure(1,weight=1)
        ttk.Label(setup,text='내 세이펜 PIN').grid(row=0,column=0,sticky='w',padx=(0,12))
        ttk.Entry(setup,textvariable=self.template,state='readonly').grid(row=0,column=1,sticky='ew')
        self._button(setup,'파일 선택',self._pick_template).grid(row=0,column=2,padx=(8,0))
        ttk.Label(setup,text='세이펜 SD카드의 BOOK 폴더에 있는 정상 PIN을 선택하세요. 앱에는 템플릿이 포함되어 있지 않습니다.',foreground='#586b7e').grid(row=1,column=0,columnspan=3,sticky='w',pady=(6,9))
        ttk.Label(setup,text='결과 저장 폴더').grid(row=2,column=0,sticky='w')
        ttk.Entry(setup,textvariable=self.folder,state='readonly').grid(row=2,column=1,sticky='ew')
        self._button(setup,'폴더 선택',self._pick_folder).grid(row=2,column=2,padx=(8,0))
        bar=ttk.Frame(root);bar.pack(fill='x',pady=(12,7))
        ttk.Label(bar,text='2  책과 음원',font=('맑은 고딕',12,'bold')).pack(side='left',padx=(0,12))
        self.combo=ttk.Combobox(bar,textvariable=self.catalog_name,values=[c.title for c in self.catalogs],state='readonly',width=32)
        self.combo.pack(side='left');self.combo.bind('<<ComboboxSelected>>',self._switch_catalog)
        self._button(bar,'책 목록 추가…',self._import_catalog).pack(side='right')
        self._button(bar,'음원 폴더에서 자동 찾기…',self._autofill).pack(side='right',padx=8)
        middle=ttk.Frame(root);middle.pack(fill='both',expand=True)
        left=ttk.Frame(middle);left.pack(side='left',fill='y',padx=(0,16))
        self.tree=ttk.Treeview(left,columns=('ready',),show='tree headings',selectmode='browse',height=8)
        self.tree.heading('#0',text='책 이름');self.tree.heading('ready',text='선택')
        self.tree.column('#0',width=235,minwidth=140);self.tree.column('ready',width=52,stretch=False,anchor='center')
        ys=ttk.Scrollbar(left,command=self.tree.yview);self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side='left',fill='both',expand=True);ys.pack(side='right',fill='y')
        self.tree.bind('<<TreeviewSelect>>',self._show_book)
        right=ttk.Frame(middle);right.pack(side='left',fill='both',expand=True)
        self.book_title=ttk.Label(right,text='',font=('맑은 고딕',15,'bold'));self.book_title.pack(anchor='w',pady=(2,8))
        self.canvas=tk.Canvas(right,bg=BG,highlightthickness=0)
        scroll=ttk.Scrollbar(right,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True)
        self.form=ttk.Frame(self.canvas);self.window=self.canvas.create_window((0,0),window=self.form,anchor='nw')
        self.form.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.window,width=e.width))
        ttk.Label(root,textvariable=self.count,foreground='#586b7e').pack(anchor='w',pady=(10,5))
        self.consent=ttk.Checkbutton(root,text='선택한 책·음원과 세이펜 PIN을 적법하게 보유하거나 이용할 권한이 있습니다.',variable=self.rights)
        self.consent.pack(anchor='w',pady=(0,8));self.controls.append(self.consent)
        footer=ttk.Frame(root);footer.pack(fill='x')
        self.create=self._button(footer,'선택한 음원으로 PIN 만들기',self._start,style='Primary.TButton');self.create.pack(side='right')
        self.cancel=ttk.Button(footer,text='취소',command=self.cancel_event.set,state='disabled');self.cancel.pack(side='right',padx=8)
        self.open_button=ttk.Button(footer,text='결과 폴더 열기',command=self._open_output,state='disabled');self.open_button.pack(side='left')
        self.progress=ttk.Progressbar(root,mode='determinate');self.progress.pack(fill='x',pady=(8,5))
        self.status_label=ttk.Label(root,textvariable=self.status,wraplength=950)
        self.status_label.pack(anchor='w')
        # Reserve the footer at small window sizes; only the book area shrinks.
        root.columnconfigure(0,weight=1)
        layout=[(widget,widget.pack_info()) for widget in root.winfo_children()]
        for widget,_ in layout:widget.pack_forget()
        for index,(widget,packing) in enumerate(layout):
            widget.grid(row=index,column=0,sticky='nsew' if widget is middle else 'ew',
                        pady=packing.get('pady',0))
            if widget is middle:root.rowconfigure(index,weight=1)

    def _pick_template(self):
        if self.busy:return
        p=filedialog.askopenfilename(title='내 세이펜의 정상 PIN 선택',filetypes=[('세이펜 PIN','*.pin')])
        if p:self.template.set(p)

    def _pick_folder(self):
        if self.busy:return
        p=filedialog.askdirectory(title='결과를 저장할 PC 폴더 선택')
        if p:self.folder.set(p)

    def _populate_books(self):
        self.books=list(dict.fromkeys(t.book for t in self.catalog.tracks))
        self.tree.delete(*self.tree.get_children())
        for i,book in enumerate(self.books):self.tree.insert('', 'end',iid=str(i),text=book,values=('0',))
        self.tree.selection_set('0');self._show_book()

    def _show_book(self,event=None):
        choice=self.tree.selection()
        if not choice:return
        book=self.books[int(choice[0])];self.book_title.configure(text=book)
        for child in self.form.winfo_children():child.destroy()
        self.path_vars={}
        for track in (t for t in self.catalog.tracks if t.book==book):
            frame=ttk.Frame(self.form,padding=(0,2,8,4));frame.pack(fill='x')
            ttk.Label(frame,text=track.mode,font=('맑은 고딕',10,'bold')).pack(anchor='w')
            value=tk.StringVar(value=str(self.selected.get(track.key,'아직 선택하지 않았습니다')))
            self.path_vars[track.key]=value
            ttk.Entry(frame,textvariable=value,state='readonly').pack(fill='x',pady=(3,3))
            row=ttk.Frame(frame);row.pack(fill='x')
            pick=ttk.Button(row,text='MP3 선택',style='Small.TButton',command=lambda t=track:self._pick_audio(t));pick.pack(side='left')
            clear=ttk.Button(row,text='선택 해제',style='Small.TButton',command=lambda t=track:self._clear_audio(t));clear.pack(side='left',padx=6)
            if self.busy:pick.state(['disabled']);clear.state(['disabled'])
        self.canvas.yview_moveto(0)

    def _pick_audio(self,track):
        if self.busy:return
        p=filedialog.askopenfilename(title=f'{track.book} · {track.mode} MP3 선택',filetypes=[('MP3 음원','*.mp3')])
        if p:self.selected[track.key]=p;self.path_vars[track.key].set(p);self._refresh_count()

    def _clear_audio(self,track):
        if self.busy:return
        self.selected.pop(track.key,None);self.path_vars[track.key].set('아직 선택하지 않았습니다');self._refresh_count()

    def _refresh_count(self):
        self.count.set(f'{len(self.selected)} / {len(self.catalog.tracks)}개 음원 선택 · 선택하지 않은 항목은 만들지 않습니다.')
        for i,book in enumerate(self.books):
            tracks=[t for t in self.catalog.tracks if t.book==book]
            self.tree.set(str(i),'ready',f'{sum(t.key in self.selected for t in tracks)}/{len(tracks)}')

    def _switch_catalog(self,event=None):
        if self.busy:return
        self.catalog=self.catalogs[self.combo.current()];self.selected={};self._populate_books();self._refresh_count()

    def _import_catalog(self):
        if self.busy:return
        p=filedialog.askopenfilename(title='책 목록 JSON 추가',filetypes=[('책 목록','*.json')])
        if not p:return
        try:catalog=load_catalog(p)
        except (OSError,CatalogError) as e:messagebox.showerror('책 목록을 열 수 없습니다',str(e));return
        if any(c.id==catalog.id for c in self.catalogs):
            messagebox.showinfo('이미 있는 책 목록','같은 ID의 책 목록이 이미 있습니다.');return
        self.catalogs.append(catalog);self.combo.configure(values=[c.title for c in self.catalogs])
        self.combo.current(len(self.catalogs)-1);self._switch_catalog()

    def _autofill(self):
        if self.busy:return
        p=filedialog.askdirectory(title='보유 MP3가 들어 있는 폴더 선택')
        if not p:return
        needed={t.suggested_filename.lower() for t in self.catalog.tracks if t.suggested_filename}
        found={};count=0;limited=False
        for base,dirs,files in os.walk(p,followlinks=False):
            for name in files:
                count+=1
                if name.lower() in needed:found.setdefault(name.lower(),[]).append(str(Path(base)/name))
            if count>100000:limited=True;break
        added=0;duplicates=0
        for track in self.catalog.tracks:
            if track.key in self.selected:continue
            matches=found.get(track.suggested_filename.lower(),[])
            if len(matches)==1:self.selected[track.key]=matches[0];added+=1
            elif len(matches)>1:duplicates+=1
        self._show_book();self._refresh_count()
        self.status.set(f'{added}개 음원을 연결했습니다. 중복 파일명 {duplicates}개는 직접 선택해 주세요.'+(' 검색 범위가 커서 일부만 확인했습니다.' if limited else ''))

    def _set_busy(self,value):
        self.busy=value
        for control in self.controls:control.state(['disabled'] if value else ['!disabled'])
        self.combo.configure(state='disabled' if value else 'readonly')
        self.cancel.configure(state='normal' if value else 'disabled')
        self._show_book()

    def _start(self):
        if self.busy:return
        if not self.template.get() or not self.folder.get() or not self.selected:
            messagebox.showinfo('준비 파일을 확인하세요','세이펜 PIN, 저장 폴더, MP3 한 개 이상을 선택해 주세요.');return
        if not self.rights.get():
            messagebox.showinfo('사용할 파일을 확인하세요','책과 음원, 세이펜 PIN을 이용할 권한이 있는지 확인하고 체크해 주세요.');return
        self.cancel_event.clear();self.output=None;self.open_button.configure(state='disabled');self.progress['value']=0
        catalog=self.catalog;template=self.template.get();selected=dict(self.selected);folder=self.folder.get()
        self._set_busy(True)
        def work():
            try:
                result=build_batch(catalog,template,selected,folder,rights_confirmed=True,cancel=self.cancel_event,
                                   progress=lambda *args:self.events.put(('progress',args)))
                self.events.put(('done',result))
            except Cancelled as e:self.events.put(('cancelled',str(e)))
            except (UserError,OSError) as e:self.events.put(('error',str(e)))
            except Exception:self.events.put(('error','예상하지 못한 오류로 중단했습니다. 원본은 그대로입니다. 다른 저장 폴더에서 다시 시도해 주세요.'))
        threading.Thread(target=work,daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='progress':
                    text,done,total=data;self.status.set(text);self.progress['maximum']=total;self.progress['value']=done
                elif kind=='done':
                    self.output,report=data;self._set_busy(False);self.open_button.configure(state='normal')
                    self.status.set(f"완료! {len(report['files'])}개 PIN · {report['total_bytes']/1000000:.1f}MB · 결과 폴더의 안내를 확인하세요.")
                    messagebox.showinfo('PIN 생성 완료','결과 폴더의 BOOK 안에 있는 PIN을 사용하세요.\n기존 SD카드를 백업하고, 같은 이름의 파일을 보관한 뒤 교체하세요.')
                else:
                    self._set_busy(False);self.status.set(data)
                    if kind=='error':messagebox.showerror('PIN을 만들지 못했습니다',data)
        except queue.Empty:pass
        self.after(100,self._poll)

    def _open_output(self):
        if self.output and self.output.is_dir():os.startfile(str(self.output))

    def _close(self):
        if self.busy:
            self.cancel_event.set();self.status.set('취소하고 있습니다. 작업이 멈춘 뒤 창을 닫아 주세요.');return
        self.destroy()

def main():
    if os.name=='nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError,AttributeError):pass
    App().mainloop()
