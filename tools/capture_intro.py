"""소개 자료용 화면 캡처: 헤드리스 Chromium(CDP), 1440x900 논리 해상도 · 2배 픽셀, 밝은 테마.
사용: python tools/capture_intro.py OUTDIR [BASE_URL]   (환경변수 GW=게이트웨이 idx, BED=침상 row, ONLY=이름 일부, REPEAT=파형 스윕 재촬영 횟수; chromium · websockets 필요)
"""
import asyncio, base64, json, os, subprocess, sys, time, urllib.request
import websockets

OUT = sys.argv[1]
URL = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:5445/"
os.makedirs(OUT, exist_ok=True)
PORT = 9341
W, H, DPR = 1440, 900, 2
FLAGS = ['--disable-background-networking', '--disable-component-update', '--disable-default-apps', '--disable-extensions', '--disable-sync', '--no-first-run',
         '--disable-client-side-phishing-detection', '--safebrowsing-disable-auto-update', '--password-store=basic', '--hide-scrollbars',
         '--disable-features=Translate,OptimizationHints,MediaRouter,DialMediaRouteProvider']
chrome = subprocess.Popen(['/usr/bin/chromium', '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', *FLAGS,
                           f'--remote-debugging-port={PORT}', f'--user-data-dir={OUT}/.prof', f'--window-size={W},{H}', 'about:blank'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

GW_BUSY, BED_ROW = int(os.environ.get("GW", "116")), int(os.environ.get("BED", "78"))

# (파일 이름, 경로, 준비 JS 목록[(js, 대기초)], 잘라낼 요소(None=화면 전체), 최대 높이(논리 px))
SHOTS = [
    ("key/01_dashboard", "?tab=dash", [], None, None),
    ("key/02_hospital_floorplan", "?tab=hosp", [("document.querySelector('#tabs button[data-tab=hosp]').click()", 6)], ".planmain", 900),
    ("key/03_central_station_3x3", f"?tab=hosp&gw={GW_BUSY}&preset=3x3", [("new Promise(r=>{const t0=Date.now();const f=()=>{const g=document.querySelector('#monGrid');if(g&&g.children.length>1&&!g.querySelector('.cs-empty')||Date.now()-t0>40000)r(1);else setTimeout(f,500)};f()})", 6)], "#monModal", 900),
    ("key/04_bedside_viewer", f"?tab=hosp&gw={GW_BUSY}&preset=3x3&bed={BED_ROW}", [("new Promise(r=>{const t0=Date.now();const f=()=>{const g=document.querySelector('#monGrid');if(g&&g.children.length>1&&!g.querySelector('.cs-empty')||Date.now()-t0>40000)r(1);else setTimeout(f,500)};f()})", 6), ("1", 16)], "#vmModal", 900),
    ("key/05_monitoring_patient", "?tab=pat", [("document.querySelector('#tabs button[data-tab=mon]').click()", 4),
                                              ("document.querySelector('#patHead button[data-sort=rx_days]').click();document.querySelector('#patHead button[data-sort=rx_days]').click()", 2),
                                              ("document.querySelector('#patBody .prow').click()", 6)], None, None),
    ("key/06_emr_integration", "?tab=emr", [("document.querySelector('#tabs button[data-tab=emr]').click()", 5)], None, None),
    ("screens/07_floorplan_room_zoom", "?tab=hosp", [("document.querySelector('#tabs button[data-tab=hosp]').click()", 6),
                                                     ("(()=>{const s=document.querySelector('#floorMap');const R=s.getBoundingClientRect();const q=[...s.querySelectorAll('polygon[data-rid]')].find(p=>(p.querySelector('title')||{}).textContent?.includes('병실'));const b=q.getBoundingClientRect();const x=b.x+b.width*0.2,y=b.y+b.height*0.2;const o={bubbles:true,clientX:x,clientY:y,pointerId:1,button:0,pointerType:'mouse'};s.dispatchEvent(new PointerEvent('pointerdown',o));s.dispatchEvent(new PointerEvent('pointerup',o));})()", 2)], ".planmain", 900),
    ("screens/08_central_station_24", f"?tab=hosp&gw={GW_BUSY}&preset=4x6", [("new Promise(r=>{const t0=Date.now();const f=()=>{const g=document.querySelector('#monGrid');if(g&&g.children.length>1&&!g.querySelector('.cs-empty')||Date.now()-t0>40000)r(1);else setTimeout(f,500)};f()})", 6)], "#monModal", 900),
    ("screens/09_numeric_board_96", f"?tab=hosp&gw={GW_BUSY}&preset=12x8", [("new Promise(r=>{const t0=Date.now();const f=()=>{const g=document.querySelector('#monGrid');if(g&&g.children.length>1&&!g.querySelector('.cs-empty')||Date.now()-t0>40000)r(1);else setTimeout(f,500)};f()})", 6)], "#monModal", 360),
    ("screens/10_scenario_presets", "?tab=scn", [], None, None),
    ("screens/11_signal_tx", "?tab=tx", [], None, None),
    ("screens/12_medical_world", "?tab=struct", [], None, None),
    ("screens/13_field_test", "?tab=test", [("document.querySelector('#tabs button[data-tab=test]').click()", 4)], None, None),
    ("screens/14_data_registry", "?tab=data", [("document.querySelector('#tabs button[data-tab=data]').click()", 5)], None, None),
    ("screens/15_event_log", "?tab=log", [("document.querySelector('#tabs button[data-tab=log]').click()", 3), ("document.querySelector('#logSeg button[data-lv=ev]').click()", 4)], None, None),
    ("screens/16_protocol", "?tab=proto", [], 'section[data-tab="proto"]', 900),
    ("screens/17_start_manual", "?tab=guide", [], 'section[data-tab="guide"]', 900),
    ("screens/18_hospital_lists", "?tab=hosp", [("document.querySelector('#tabs button[data-tab=hosp]').click()", 6),
                                                ("document.querySelector('#tripBody')?.closest('.card')?.scrollIntoView({block:'start'})", 2)], "#tripBody", 900),
    ("screens/19_emr_site_detail", "?tab=emr", [("document.querySelector('#tabs button[data-tab=emr]').click()", 5),
                                                ("document.querySelector('#emrTbl tbody tr[data-id=jp-toto]').click()", 2),
                                                ("document.querySelector('#emrSamples').click()", 2),
                                                ("document.querySelector('#emrDetail').scrollIntoView({block:'start'})", 1)], "#emrDetail", 1100),
]


async def main():
    for _ in range(60):
        try:
            page = next(t for t in json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json')) if t['type'] == 'page'); break
        except Exception:
            time.sleep(0.25)
    async with websockets.connect(page['webSocketDebuggerUrl'], max_size=200_000_000) as ws:
        n = [0]

        async def cmd(m, **p):
            n[0] += 1; i = n[0]
            await ws.send(json.dumps({'id': i, 'method': m, 'params': p}))
            while True:
                r = json.loads(await asyncio.wait_for(ws.recv(), 60))
                if r.get('id') == i:
                    return r.get('result', {})

        async def js(e):
            return (await cmd('Runtime.evaluate', expression=e, returnByValue=True, awaitPromise=True)).get('result', {}).get('value')

        await cmd('Page.enable'); await cmd('Runtime.enable')
        await cmd('Emulation.setDeviceMetricsOverride', width=W, height=H, deviceScaleFactor=DPR, mobile=False)
        await cmd('Page.addScriptToEvaluateOnNewDocument', source="try{localStorage.setItem('theme','light')}catch(e){};window.__errs=[];addEventListener('error',e=>__errs.push(e.message))")
        only = os.environ.get('ONLY')
        for name, path, prep, sel, maxh in SHOTS:
            if only and not any(o in name for o in only.split(',')):
                continue
            await cmd('Page.navigate', url=URL + path)
            await asyncio.sleep(7)
            for code, wait in prep:
                try:
                    await js(code)
                except Exception as e:
                    print('  prep 실패', name, e)
                await asyncio.sleep(wait)
            if sel:
                r = json.loads(await js(f"JSON.stringify((()=>{{const e=document.querySelector('{sel}'); if(!e) return null; e.scrollIntoView({{block:'start'}}); const b=e.getBoundingClientRect(); return {{x:b.x+scrollX,y:b.y+scrollY,w:b.width,h:b.height}}}})())") or "null")
                await asyncio.sleep(1)
                if r:
                    pad = 0 if sel in ('#monModal', '#vmModal') else 8
                    clip = {'x': max(0, r['x'] - pad), 'y': max(0, r['y'] - pad), 'width': min(r['w'] + 2 * pad, W), 'height': min(maxh or r['h'], r['h']) + 2 * pad, 'scale': 1}
                    s = await cmd('Page.captureScreenshot', format='png', clip=clip, captureBeyondViewport=sel not in ('#monModal', '#vmModal'))
                    for _ in range(int(os.environ.get('REPEAT', '1')) - 1):     # 파형 스윕: 가장 많이 그려진 순간을 고른다
                        await asyncio.sleep(2.2)
                        s2 = await cmd('Page.captureScreenshot', format='png', clip=clip, captureBeyondViewport=sel not in ('#monModal', '#vmModal'))
                        if len(s2['data']) > len(s['data']):
                            s = s2
                else:
                    print('  요소 없음', sel); s = await cmd('Page.captureScreenshot', format='png')
            else:
                await js("window.scrollTo(0,0)")
                s = await cmd('Page.captureScreenshot', format='png')
            fp = os.path.join(OUT, name + '.png')
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, 'wb').write(base64.b64decode(s['data']))
            print(name, os.path.getsize(fp) // 1024, 'KB')
        print('JS 오류:', await js('JSON.stringify(window.__errs||null)'))


try:
    asyncio.run(main())
finally:
    chrome.terminate()
