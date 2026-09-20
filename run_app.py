"""Windows GUI entry point used by Python and PyInstaller."""
import sys
from saypen_pin.app import main

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test':
        import json
        from pathlib import Path
        from saypen_pin.app import App
        app=App();app.withdraw();app.update()
        report={'gui_started':True,'catalogs':len(app.catalogs),'tracks':len(app.catalog.tracks),
                'bundled':bool(getattr(sys,'frozen',False))}
        with Path(sys.argv[2]).open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
        app.destroy()
    else:
        main()
