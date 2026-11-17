# Run with the local frontend/backend running and agent-browser installed.
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
    if (-not $reply.success) { throw $reply.error }
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
    $homeWidth = Evaluate 'document.querySelector(".sidebar").getBoundingClientRect().width'
    Evaluate '(()=>{const spacer=document.createElement("div");spacer.id="layout-scroll-probe";spacer.style.height="2000px";document.querySelector(".main-shell").append(spacer);window.scrollTo(0,600)})()' | Out-Null
    Browser wait --fn 'window.scrollY >= 600' | Out-Null
    Check (Evaluate 'Math.abs(document.querySelector(".sidebar").getBoundingClientRect().top) < 1') 'Navigation must stay at the viewport top when the page scrolls.'
    Check (Evaluate 'document.querySelector(".sidebar-inner").scrollTop === 0') 'Page scrolling must not move navigation contents.'
    Check (Evaluate '(()=>{const brand=document.querySelector(".brand"),r=brand.getBoundingClientRect();return r.top>=0 && document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)?.closest(".brand")===brand})()') 'Navigation branding must remain visible and reachable after scrolling.'
    Evaluate '(()=>{const spacer=document.createElement("div");spacer.id="layout-nav-probe";spacer.style.height="2000px";document.querySelector(".sidebar-sections").append(spacer);document.querySelector(".sidebar-inner").scrollTop=200})()' | Out-Null
    Check (Evaluate 'document.querySelector(".sidebar-inner").scrollTop >= 200 && Math.abs(window.scrollY - 600) < 1') 'Navigation must scroll independently when it overflows.'
    Evaluate 'document.querySelector("#layout-scroll-probe").remove();document.querySelector("#layout-nav-probe").remove();document.querySelector(".sidebar-inner").scrollTop=0;window.scrollTo(0,0)' | Out-Null
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
    Check (Evaluate '!Array.from(document.querySelectorAll(".navigation button")).some(b => b.textContent.includes("搜索会话"))') 'Chat navigation must not add a search item.'
    Browser press Control+k | Out-Null
    Browser wait --fn '!!document.querySelector("input[name=password]")' | Out-Null
    Check (Evaluate '(()=>{const input=document.querySelector("input[name=password]");input.focus();return getComputedStyle(input).backgroundColor === "rgba(0, 0, 0, 0)"})()') 'Focused password input must stay transparent.'
    Browser set viewport 390 844 | Out-Null
    Check (Evaluate '(()=>{const r=document.querySelector("[role=dialog]").getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight})()') 'Login card must fit the mobile viewport.'
    Browser press Escape | Out-Null
    Browser open "$BaseUrl/" | Out-Null
    Browser wait --fn '!!document.querySelector("#home-overview")' | Out-Null
    Browser find role button click --name '展开资料概况' --exact | Out-Null
    Browser wait --fn 'document.querySelector(".home-panel").getBoundingClientRect().width > 300' | Out-Null
    Check (Evaluate '(()=>{const r=document.querySelector(".home-panel").getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && document.body.scrollWidth<=innerWidth})()') 'Mobile overview drawer must fit without horizontal overflow.'
    Write-Output 'Layout regression checks passed.'
} finally {
    Browser close | Out-Null
}
