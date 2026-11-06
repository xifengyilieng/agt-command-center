"""Install a local, selectable Wallpaper Engine web project. Never apply a desktop."""
from pathlib import Path
import argparse
import json
import shutil
import re
import secrets

ROOT=Path(__file__).resolve().parent


def find_engine():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam') as key:
        steam=Path(winreg.QueryValueEx(key,'SteamPath')[0])
    libraries=[steam]
    vdf=steam/'steamapps/libraryfolders.vdf'
    if vdf.exists():
        libraries += [Path(p.replace('\\\\','\\')) for p in re.findall(r'"path"\s+"([^"]+)"',vdf.read_text(encoding='utf-8'))]
    for library in libraries:
        engine=library/'steamapps/common/wallpaper_engine'
        if (engine/'wallpaper64.exe').exists() or (engine/'wallpaper32.exe').exists():
            return engine.resolve()
    raise FileNotFoundError('Wallpaper Engine installation not found')


def project_definition():
    properties={
        'showpanels':{'type':'bool','text':'显示底部信息栏','value':True,'order':100},
        'showheader':{'type':'bool','text':'显示顶部指标','value':True,'order':101},
        'camera':{'type':'bool','text':'自动运镜','value':True,'order':102},
        'brightness':{'type':'slider','text':'画面亮度','value':100,'min':15,'max':100,'step':1,'order':103},
        'ringspeed':{'type':'slider','text':'圆环转速','value':3,'min':0,'max':3,'step':.05,'fraction':True,'precision':2,'order':104},
    }
    return {'title':'AGT 真实任务作战桌面','type':'web','file':'index.html','preview':'preview.png','version':12,
        'description':'本机 Codex / DeepSeek Harness 真实任务桌面。保留 HALOFLUX 节拍驱动的编织光场、彗星束冲击和五层外光束，多彩环带绑定真实任务。稀疏任务连线、可操作的参数与设置、三档画质及可切换的粒子流场；底部六块可收起。编织光场与冲击是视觉效果，任务数据与关系来自本机记录。需要随附的 AGT 本机采集器。\n\nModified from HALOFLUX by Ryder191 (RYDER CODE / Ms MAIKEBAIs CAT).\nOriginal work: HALOFLUX by Ryder191 (RYDER CODE / Ms MAIKEBAIs CAT)\nSource: https://github.com/Ryder191/haloflux\nLicense: PolyForm Noncommercial 1.0.0. Noncommercial use only. No monetization.\n本地项目标识：agt-command-center',
        'general':{'properties':properties},'tags':['Technology'],'contentrating':'Everyone','ratingsex':'none','ratingviolence':'none'}


def install(engine):
    engine=Path(engine).resolve()
    if not (engine/'wallpaper64.exe').exists() and not (engine/'wallpaper32.exe').exists():
        raise ValueError('The selected folder is not a Wallpaper Engine installation')
    target=engine/'projects/myprojects/AGT-Live-Command'
    if target.exists() and any(target.iterdir()) and not (target/'project.json').exists():
        raise ValueError('A nonempty folder without an AGT project marker was not modified')
    if target.exists() and (target/'project.json').exists():
        existing=json.loads((target/'project.json').read_text(encoding='utf-8'))
        if 'agt-command-center' not in existing.get('description',''):
            raise ValueError('A different project already uses this folder; it was not modified')
    target.mkdir(parents=True,exist_ok=True)
    # CEF in WE 2.8.42 aborts when the old file page creates an HTTP iframe.
    # Render directly in the host page; only telemetry crosses to localhost.
    token_file=ROOT/'.wallpaper-token'
    if not token_file.exists():
        token_file.write_text(secrets.token_hex(32),encoding='ascii')
    token=token_file.read_text(encoding='ascii').strip()
    html=(ROOT/'index.html').read_text(encoding='utf-8')
    html=html.replace('href="/screen.css?v=4"','href="screen.css?v=5"')
    html=html.replace('src="/runtime.js?v=4"','src="runtime.js?v=5"')
    html=html.replace('</head>','<script src="connection.js"></script>\n</head>')
    (ROOT/'wallpaper').mkdir(exist_ok=True)
    (ROOT/'wallpaper/index.html').write_text(html,encoding='utf-8')
    (target/'index.html').write_text(html,encoding='utf-8')
    (target/'connection.js').write_text(
        'window.agtWallpaperHost='+json.dumps({'api':'http://127.0.0.1:8767','token':token,
            'properties':{},'general':{'fps':60},'paused':False,'native':False})+';\n'
        'window.wallpaperPropertyListener={'
        'applyUserProperties:function(p){window.agtWallpaperHost.native=true;Object.assign(window.agtWallpaperHost.properties,p);},'
        'applyGeneralProperties:function(p){window.agtWallpaperHost.native=true;Object.assign(window.agtWallpaperHost.general,p);},'
        'setPaused:function(p){window.agtWallpaperHost.native=true;window.agtWallpaperHost.paused=!!p;}};\n',encoding='utf-8')
    shutil.copy2(ROOT/'runtime.js',target/'runtime.js')
    (target/'screen.css').write_text((ROOT/'screen.css').read_text(encoding='utf-8').replace("url('/fonts/","url('fonts/"),encoding='utf-8')
    (target/'fonts').mkdir(exist_ok=True)
    for font in (ROOT/'vendor/fonts').glob('*.ttf'):
        shutil.copy2(font,target/'fonts'/font.name)
    shutil.copy2(ROOT/'vendor/fonts/OFL.txt',target/'fonts/OFL.txt')
    # Remove only this project's previously bundled soundtrack on upgrades.
    # Keep upstream sources and any unrelated files in the installed directory.
    (target/'audio/sw421k.mp3').unlink(missing_ok=True)
    shutil.copy2(ROOT/'vendor/haloflux/LICENSE',target/'LICENSE')
    shutil.copy2(ROOT/'UPSTREAM.txt',target/'NOTICE.txt')
    # The shared package contains no screenshots of the publisher's sessions.
    preview=ROOT/'docs/preview.png'
    if not preview.exists():
        preview=ROOT/'vendor/haloflux/preview.png'
    shutil.copy2(preview,target/'preview.png')
    (target/'project.json').write_text(json.dumps(project_definition(),ensure_ascii=False,indent=2),encoding='utf-8')
    (target/'说明.txt').write_text('在 Wallpaper Engine 已安装列表中选择「AGT 真实任务作战桌面」。\n实时数据由本机 AGT 采集器提供：'+str(ROOT)+'\n需要时双击该目录的「启动采集器.cmd」。\n画面直接在壁纸宿主中渲染，兼容 2.8.42，无 iframe；使用安装令牌从 127.0.0.1 只读获取任务数据。\n',encoding='utf-8')
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--we-dir');args=parser.parse_args()
    result=install(args.we_dir or find_engine())
    print(json.dumps({'installed':str(result),'title':project_definition()['title']},ensure_ascii=False))
