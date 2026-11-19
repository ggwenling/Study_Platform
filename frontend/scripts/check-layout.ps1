# Run in PowerShell 7 with the local frontend/backend running and agent-browser installed.
param([string]$BaseUrl = 'http://127.0.0.1:5173')
$ErrorActionPreference = 'Stop'
$layoutSession = 'layout-' + [guid]::NewGuid().ToString('N')
$browserExecutable = Join-Path (Split-Path (Get-Command agent-browser).Source) 'node_modules/agent-browser/bin/agent-browser-win32-x64.exe'
function Browser {
    # Read one JSON reply instead of waiting for EOF: a newly launched daemon may inherit the output pipe.
    $start = [Diagnostics.ProcessStartInfo]::new($browserExecutable)
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in @('--session', $layoutSession, '--json') + $args) { $start.ArgumentList.Add([string]$argument) }
    $process = [Diagnostics.Process]::Start($start)
    $line = $process.StandardOutput.ReadLine()
    if (-not $process.WaitForExit(30000)) { $process.Kill(); throw 'Browser command timed out.' }
    $process.Dispose()
    $reply = $line | ConvertFrom-Json
    if (-not $reply.success) { throw "Browser $($args[0]) failed: $($reply.error)" }
    return $reply.data
}
function Evaluate([string]$Code) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Code))
    return (Browser eval -b $encoded).result
}
function Check([bool]$Pass, [string]$Message) {
    if (-not $Pass) { throw $Message }
}
try {
    Browser set viewport 1246 955 | Out-Null
    Browser open "$BaseUrl/" | Out-Null
    Browser wait --fn '!!document.querySelector(".home-panel-inner")' | Out-Null
    Evaluate 'document.fonts.ready.then(()=>true)' | Out-Null
    Browser wait --fn 'document.querySelector(".home-orbit")?.getAnimations()[0]?.playState === "running"' | Out-Null
    Check (Evaluate '(()=>{const [a,...extra]=document.querySelector(".home-orbit").getAnimations();return !extra.length && a.effect.getTiming().duration===14000 && a.effect.getTiming().direction==="alternate" && a.effect.getKeyframes().every(k=>k.transform && k.opacity===undefined)})()') 'The desktop orbit must use a single 28-second transform-only cycle.'
    $homeWidth = Evaluate 'document.querySelector(".sidebar").getBoundingClientRect().width'
    Evaluate '(()=>{const spacer=document.createElement("div");spacer.id="layout-scroll-probe";spacer.style.height="2000px";document.querySelector(".main-shell").append(spacer);window.scrollTo(0,600)})()' | Out-Null
    Browser wait --fn 'window.scrollY >= 600' | Out-Null
    Check (Evaluate 'Math.abs(document.querySelector(".sidebar").getBoundingClientRect().top) < 1') 'Navigation must stay at the viewport top when the page scrolls.'
    Check (Evaluate 'document.querySelector(".sidebar-sections").scrollTop === 0') 'Page scrolling must not move navigation contents.'
    Check (Evaluate '(()=>{const brand=document.querySelector(".brand"),r=brand.getBoundingClientRect();return r.top>=0 && document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)?.closest(".brand")===brand})()') 'Navigation branding must remain visible and reachable after scrolling.'
    Evaluate '(()=>{const spacer=document.createElement("div");spacer.id="layout-nav-probe";spacer.style.height="2000px";document.querySelector(".sidebar-sections").append(spacer);document.querySelector(".sidebar-sections").scrollTop=200})()' | Out-Null
    Check (Evaluate 'getComputedStyle(document.querySelector(".sidebar-sections"),"::-webkit-scrollbar").width === "6px"') 'The sidebar should have a compact scrollbar in Chromium.'
    Check (Evaluate 'document.querySelector(".sidebar-sections").scrollTop >= 200 && Math.abs(window.scrollY - 600) < 1') 'Navigation must scroll independently when it overflows.'
    Check (Evaluate '(()=>{const menu=document.querySelector(".workspace-label"),r=menu.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight})()') 'The account menu must stay visible while projects scroll.'
    Evaluate 'window.scrollTo(0,900)' | Out-Null
    Browser wait --fn 'document.querySelector(".home-orbit").getAnimations()[0]?.playState === "paused"' | Out-Null
    Evaluate 'document.querySelector("#layout-scroll-probe").remove();document.querySelector("#layout-nav-probe").remove();document.querySelector(".sidebar-sections").scrollTop=0;window.scrollTo(0,0)' | Out-Null
    Browser wait --fn 'document.querySelector(".home-orbit").getAnimations()[0]?.playState === "running"' | Out-Null
    Browser find role button click --name '收起资料概况' --exact | Out-Null
    Browser wait --fn 'Math.abs(document.querySelector(".home-panel").getBoundingClientRect().width - 36) < 1' | Out-Null
    Check (Evaluate 'document.querySelector("#home-overview").inert') 'Collapsed overview must be inert.'
    Browser find role button click --name '最近对话' --exact | Out-Null
    Browser wait --fn 'document.querySelector(".recent button").getAttribute("aria-expanded") === "false"' | Out-Null
    Browser open "$BaseUrl/" | Out-Null
    Browser wait --fn '!!document.querySelector(".recent button")' | Out-Null
    Check (Evaluate 'document.querySelector(".recent button").getAttribute("aria-expanded") === "false"') 'Recent disclosure must survive reload.'
    Check (Evaluate 'document.querySelector("#home-overview").inert') 'Overview disclosure must survive reload.'
    Browser open "$BaseUrl/chat/demo" | Out-Null
    Browser wait --fn '!!document.querySelector(".chat-footer")' | Out-Null
    $chatWidth = Evaluate 'document.querySelector(".sidebar").getBoundingClientRect().width'
    Check ($homeWidth -eq $chatWidth) 'Navigation width must stay unchanged on a chat route.'
    Check (Evaluate 'document.querySelector(".chat-header button[aria-label=全局搜索]").innerText.trim() === ""') 'Chat must expose an icon-only search action.'
    Check (Evaluate '!Array.from(document.querySelectorAll(".navigation button")).some(b => b.textContent.includes("搜索会话"))') 'Chat navigation must not add a search item.'
    Browser press Control+k | Out-Null
    Browser wait --fn '!!document.querySelector("input[name=password]")' | Out-Null
    Check (Evaluate '(()=>{const input=document.querySelector("input[name=password]");input.focus();return getComputedStyle(input).backgroundColor === "rgba(0, 0, 0, 0)"})()') 'Focused password input must stay transparent.'
    Browser set viewport 390 844 | Out-Null
    Check (Evaluate '(()=>{const r=document.querySelector("[role=dialog]").getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight})()') 'Login card must fit the mobile viewport.'
    Browser press Escape | Out-Null
    Browser set viewport 1246 955 | Out-Null
    foreach ($route in 'history', 'knowledge', 'learning', 'settings') {
        Browser open "$BaseUrl/$route" | Out-Null
        Browser wait --fn '!!document.querySelector(".sidebar")' | Out-Null
        Check ((Evaluate 'document.querySelector(".sidebar").getBoundingClientRect().width') -eq $homeWidth) "Navigation width must match Home on $route."
    }
    Browser set viewport 390 844 | Out-Null
    Browser open "$BaseUrl/" | Out-Null
    Browser wait --fn '!!document.querySelector("#home-overview")' | Out-Null
    Check (Evaluate 'document.querySelector(".home-orbit").getAnimations().length === 0 && getComputedStyle(document.querySelector(".home-orbit")).display !== "none"') 'Mobile must keep a static arc.'
    Browser find role button click --name '展开资料概况' --exact | Out-Null
    Browser wait --fn 'document.querySelector(".home-panel").getBoundingClientRect().width > 300' | Out-Null
    Check (Evaluate '(()=>{const r=document.querySelector(".home-panel").getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && document.body.scrollWidth<=innerWidth})()') 'Mobile overview drawer must fit without horizontal overflow.'

    # Authenticate against the in-browser fixture. The geometry probe never changes the local database.
    Browser set viewport 1246 500 | Out-Null
    Browser open "$BaseUrl/" | Out-Null
    Browser wait --fn '!!document.querySelector(".home-content")' | Out-Null
    Evaluate (Get-Content (Join-Path $PSScriptRoot 'fixtures/settings.js') -Raw) | Out-Null
    Evaluate (Get-Content (Join-Path $PSScriptRoot 'fixtures/library-layout.js') -Raw) | Out-Null
    Evaluate 'window.settingsPreview.libraryName = "【模拟】青禾跨年度研究项目原始资料与访谈记录档案"' | Out-Null
    Browser click '.workspace-label button' | Out-Null
    Browser wait --fn '!!document.querySelector("input[name=password]")' | Out-Null
    Browser fill 'input[name=email]' 'settings@example.test' | Out-Null
    Browser fill 'input[name=password]' 'PreviewSettings123' | Out-Null
    Browser click '[role=dialog] button[type=submit]' | Out-Null
    Browser wait --fn 'document.querySelector(".composer-select[data-value-align=center]")?.title.includes("青禾") && !document.querySelector("[role=dialog]")' | Out-Null
    Check (Evaluate '(()=>{const b=document.querySelector(".composer-select"),v=b.querySelector(":scope > span[aria-hidden=true]:has(span)"),r=b.getBoundingClientRect(),t=v.getBoundingClientRect();return getComputedStyle(v).textAlign==="center" && Math.abs(t.left+t.width/2-r.left-r.width/2)<=1 && Math.abs(t.top+t.height/2-r.top-r.height/2)<=1})()') 'The selected library must be centered within the entire button.'
    Browser wait --fn 'document.querySelector(".home-orbit")?.getAnimations()[0]?.playState === "running"' | Out-Null
    Evaluate 'window.layoutOrbit = document.querySelector(".home-orbit").getAnimations()[0]' | Out-Null
    Browser click '.navigation a[href="/history"]' | Out-Null
    Browser wait --fn 'document.querySelectorAll(".history-list li").length >= 8 && getComputedStyle(document.querySelector(".workspace-page")).opacity === "1"' | Out-Null
    Check (Evaluate '!document.querySelector(".home-orbit") && window.layoutOrbit.playState==="idle"') 'Leaving Home must stop and dispose of the orbit animation.'
    Check (Evaluate 'document.scrollingElement.scrollHeight > document.documentElement.clientHeight') 'History must overflow for the scroll-lock regression check.'
    $menu = '.history-list li:nth-child(4) button[aria-haspopup=menu]'
    $rect = '(()=>{const r=document.querySelector(".history-page").getBoundingClientRect();return {left:r.left,width:r.width,scroll:window.scrollY}})()'
    Evaluate 'window.scrollTo(0,40)' | Out-Null
    $before = Evaluate $rect
    Browser click $menu | Out-Null
    Browser wait --fn '!!document.querySelector("[role=menu][data-state=open]")' | Out-Null
    $opened = Evaluate $rect
    Check (([math]::Abs($before.left - $opened.left) -le 1) -and ([math]::Abs($before.width - $opened.width) -le 1) -and ($before.scroll -eq $opened.scroll)) 'Opening a history menu must preserve page width, position, and scroll.'
    Check (Evaluate '!document.body.hasAttribute("data-scroll-locked")') 'History menus must leave page scrolling available.'
    Browser press Escape | Out-Null
    Browser wait --fn '!document.querySelector("[role=menu][data-state=open]") && document.activeElement === document.querySelector(".history-list li:nth-child(4) button[aria-haspopup=menu]")' | Out-Null
    Browser press Enter | Out-Null
    Browser press ArrowDown | Out-Null
    Browser wait --fn 'document.activeElement?.matches("[role=menuitem]")' | Out-Null
    Evaluate 'window.scrollTo(0,0)' | Out-Null
    Browser click '.history-page h1' | Out-Null
    Browser wait --fn '!document.querySelector("[role=menu][data-state=open]")' | Out-Null
    Browser set media light reduced-motion | Out-Null
    Browser click '.navigation a[href="/"]' | Out-Null
    Browser wait --fn '!!document.querySelector(".home-orbit")' | Out-Null
    Check (Evaluate '(()=>{const page=getComputedStyle(document.querySelector(".workspace-page"));return page.opacity==="1" && page.top==="0px" && document.querySelector(".home-orbit").getAnimations().length===0})()') 'Reduced motion should skip the workspace entrance and keep the orbit static.'
    Browser set viewport 390 844 | Out-Null
    Check (Evaluate '(()=>{const b=document.querySelector(".composer-select"),v=b.querySelector(":scope > span[aria-hidden=true] > span"),form=document.querySelector(".composer").getBoundingClientRect(),send=document.querySelector(".send-button").getBoundingClientRect();return v.textContent.startsWith("青禾") && b.title.startsWith("【模拟】") && v.scrollWidth>v.clientWidth && getComputedStyle(v).textOverflow==="ellipsis" && send.right<=form.right && form.right<=document.documentElement.clientWidth})()') 'A long library name must truncate without clipping the composer on mobile.'

    Browser set viewport 1322 955 | Out-Null
    Browser set media dark | Out-Null
    Browser wait --fn 'document.querySelector(".current-materials-title small")?.textContent.includes("1 份可问答")' | Out-Null
    Evaluate '(()=>{window.layoutStatus=document.querySelector(".current-materials-title small");window.layoutStatusRect=layoutStatus.getBoundingClientRect();document.querySelector(".composer-select").focus({preventScroll:true})})()' | Out-Null
    Browser press Enter | Out-Null
    Browser press ArrowDown | Out-Null
    Browser press Enter | Out-Null
    Browser wait --fn 'document.querySelector(".current-materials-title small")?.textContent.includes("2 份可问答")' | Out-Null
    Check (Evaluate '(()=>{const node=document.querySelector(".current-materials-title small"),rect=node.getBoundingClientRect();return node===layoutStatus && ["x","y","height"].every(key=>Math.abs(rect[key]-layoutStatusRect[key])<=1)})()') 'Switching libraries must keep the collapsed material status in the same slot.'
    Check (Evaluate '(()=>{const project=document.querySelector(".project-folder"),title=project.querySelector(".project-name"),threads=project.querySelector(".project-conversations");return !title.querySelector("svg") && getComputedStyle(threads).borderLeftWidth==="0px" && Math.abs(title.getBoundingClientRect().left-threads.getBoundingClientRect().left)<=1})()') 'Project conversations should align under the icon-free title without a left border.'
    Browser click '.current-materials h3 button' | Out-Null
    Browser wait --fn 'document.querySelector(".current-materials [role=region]")?.getBoundingClientRect().height>=document.querySelector(".current-materials-body").offsetHeight' | Out-Null
    $expandedHeight = Evaluate 'document.querySelector(".current-materials-body").offsetHeight'
    Browser focus '.composer-select' | Out-Null
    Browser press Enter | Out-Null
    Browser press ArrowUp | Out-Null
    Browser press Enter | Out-Null
    Browser wait --fn 'document.querySelector(".current-materials-title small")?.textContent.includes("1 份可问答")' | Out-Null
    Check ((Evaluate 'parseFloat(getComputedStyle(document.querySelector(".current-materials-body")).minBlockSize)') -eq $expandedHeight) 'Switching libraries while the panel is expanded must reserve its previous height for the collapse.'

    Browser click '.navigation a[href="/knowledge"]' | Out-Null
    Browser wait --fn '!!document.querySelector(".library-heatmap [data-level-key]") && document.querySelector(".library-total")?.textContent.includes("共 0 条片段") && getComputedStyle(document.querySelector(".workspace-page")).opacity==="1"' | Out-Null
    $geometry = 'JSON.stringify([scrollY, ...[".library-main",".library-aside",".library-heatmap",".library-aside-summary",".hit-test-form",".library-filters"].flatMap(s=>{const r=document.querySelector(s).getBoundingClientRect();return [r.left,r.top,r.width,r.height].map(Math.round)})])'
    $before = Evaluate $geometry
    Browser hover '.library-heatmap [data-level-key="0"]' | Out-Null
    Browser wait --fn 'document.querySelector(".library-heatmap [role=grid]").dataset.highlight==="0"' | Out-Null
    Check ((Evaluate $geometry) -eq $before) 'Previewing a heatmap legend level must preserve card dimensions.'
    Browser mouse move 460 96 | Out-Null
    Browser wait --fn '!document.querySelector(".library-heatmap [role=grid]").dataset.highlight' | Out-Null
    Check ((Evaluate $geometry) -eq $before) 'Leaving the heatmap legend must preserve card dimensions.'
    foreach ($trigger in '.hit-test-form [role=combobox]', '.library-filters > div:nth-child(3) [role=combobox]') {
        Evaluate "document.querySelector('$trigger').scrollIntoView({block:'center'})" | Out-Null
        $before = Evaluate $geometry
        Browser click $trigger | Out-Null
        Browser wait --fn '!!document.querySelector("[role=listbox][data-state=open]")' | Out-Null
        Check ((Evaluate $geometry) -eq $before) "Opening $trigger must keep the library layout and scroll position stable."
        Browser press Escape | Out-Null
        Browser wait --fn '!document.querySelector("[role=listbox][data-state=open]") && !document.body.hasAttribute("data-scroll-locked")' | Out-Null
        Check ((Evaluate $geometry) -eq $before) "Closing $trigger must keep the library layout and scroll position stable."
    }
    Write-Output 'Layout regression checks passed.'
} finally {
    Browser close | Out-Null
}
