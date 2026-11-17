# Uses isolated, in-memory API fixtures; never creates accounts or calls a model.
param([string]$BaseUrl = 'http://127.0.0.1:5173')
$ErrorActionPreference = 'Stop'
$settingsSession = 'settings-' + [guid]::NewGuid().ToString('N')
$browserExecutable = Join-Path (Split-Path (Get-Command agent-browser).Source) 'node_modules/agent-browser/bin/agent-browser-win32-x64.exe'
function Browser {
    $start = [Diagnostics.ProcessStartInfo]::new($browserExecutable)
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in @('--session', $settingsSession, '--json') + $args) { $start.ArgumentList.Add([string]$argument) }
    $process = [Diagnostics.Process]::Start($start)
    $line = $process.StandardOutput.ReadLine()
    if (-not $process.WaitForExit(30000)) { $process.Kill(); throw 'Browser command timed out.' }
    $process.Dispose()
    $reply = $line | ConvertFrom-Json
    if (-not $reply.success) { throw "Browser $($args[0]) failed: $($reply.error) [$($args | Select-Object -Last 3)]" }
    return $reply.data
}
function Evaluate([string]$Code) { return (Browser eval -b ([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Code)))).result }
function Check([bool]$Pass, [string]$Message) { if (-not $Pass) { throw $Message } }
function OpenSettings {
    Evaluate 'document.querySelector(".navigation a[href=" + JSON.stringify("/") + "]").click()' | Out-Null
    Browser wait --fn '!!document.querySelector(".home-content")' | Out-Null
    Evaluate 'document.querySelector(".navigation a[href=" + JSON.stringify("/settings") + "]").click()' | Out-Null
    Browser wait --fn '!!document.querySelector(".model-connection") && !document.querySelector(".settings-usage").hasAttribute("hidden")' | Out-Null
}
try {
    Browser set viewport 1440 1000 | Out-Null
    Browser open "$BaseUrl/" | Out-Null
    Browser wait --fn '!!document.querySelector(".home-content")' | Out-Null
    Evaluate (Get-Content (Join-Path $PSScriptRoot 'fixtures/settings.js') -Raw) | Out-Null
    Browser click '.workspace-label button' | Out-Null
    Browser wait --fn '!!document.querySelector("input[name=password]")' | Out-Null
    Browser fill 'input[name=email]' 'settings@example.test' | Out-Null
    Browser fill 'input[name=password]' 'PreviewSettings123' | Out-Null
    Browser click '[role=dialog] button[type=submit]' | Out-Null
    Browser wait --fn '!!document.querySelector(".workspace-label button[aria-haspopup=menu]")' | Out-Null
    OpenSettings
    Write-Output 'Authenticated preview and model connection loading passed.'
    Check (Evaluate 'document.querySelector(".model-group .model-connection").id === "model-connection-current"') 'Current default must precede other connections.'
    Check (Evaluate 'document.querySelector(".model-group [data-default]").textContent.includes("测试失败")') 'A failed current default must keep its failure visible.'
    Browser wait --fn '!!document.querySelector(".settings-kv") && document.querySelector(".settings-kv").textContent.includes("80%")' | Out-Null
    Check (Evaluate 'document.querySelector(".settings-usage button[aria-expanded]").getAttribute("aria-expanded") === "false"') 'Usage details must start collapsed.'
    Browser scrollintoview '.settings-usage button[aria-expanded]' | Out-Null
    Browser find role button click --name '详细用量' --exact | Out-Null
    Check (Evaluate 'document.querySelector(".settings-usage button[aria-expanded]").getAttribute("aria-expanded") === "true"') 'Usage details must expand on demand.'
    Browser find role button click --name '项目资料库' --exact | Out-Null
    Browser wait --fn '!!document.querySelector(".project-conversations [role=button] .recent-link")' | Out-Null
    Check (Evaluate '(()=>{const row=document.querySelector(".project-conversations [role=button] .recent-link");return getComputedStyle(row).cursor==="pointer" && getComputedStyle(row.parentElement).cursor==="pointer"})()') 'Clickable conversation rows and text must use the pointer cursor.'
    Evaluate 'document.querySelector(".project-conversations [role=button]").click()' | Out-Null
    Browser wait --fn '!!document.querySelector("[role=menu][aria-label^=会话]")' | Out-Null
    Browser press Escape | Out-Null
    Check (Evaluate 'document.activeElement.matches(".project-conversations [role=button]")') 'Conversation menu must restore focus to its trigger.'
    Write-Output 'Usage overview and pointer cursor checks passed.'
    Browser set viewport 870 955 | Out-Null
    Evaluate 'window.settingsNavScroll=document.querySelector(".sidebar-inner").scrollTop;window.scrollTo(0,600)' | Out-Null
    Browser wait --fn 'window.scrollY > 100' | Out-Null
    Check (Evaluate 'Math.abs(document.querySelector(".sidebar").getBoundingClientRect().top)<1 && document.querySelector(".sidebar-inner").scrollTop===window.settingsNavScroll') 'Page scrolling must keep navigation fixed and preserve its scroll position.'
    Evaluate 'window.scrollTo(0,0)' | Out-Null
    Browser set viewport 1440 1000 | Out-Null
    Browser find role button click --name '编辑连接：资料向量连接' --exact | Out-Null
    Check (Evaluate 'document.querySelector("input[name=base_url]").readOnly && document.querySelector("input[name=model]").readOnly') 'Bound embedding address and model must remain read-only.'
    Browser press Escape | Out-Null
    Browser find role button click --name '设为默认：备用文本连接' --exact | Out-Null
    Browser wait --fn 'document.querySelector(".model-group [data-default]").textContent.includes("备用文本连接")' | Out-Null
    Write-Output 'Fixed navigation, bound editing and default switching checks passed.'
    foreach ($theme in @('light', 'dark')) {
        Browser find role button click --name '账户菜单，设置预览用户' --exact | Out-Null
        Browser find role menuitemradio click --name $(if ($theme -eq 'light') { 'Light' } else { 'Dark' }) --exact | Out-Null
        Browser press Escape | Out-Null
        foreach ($width in @(1440, 870, 768, 390)) {
            Browser set viewport $width 955 | Out-Null
            Check (Evaluate 'document.documentElement.scrollWidth<=innerWidth') "$theme settings overflows at $width px."
        }
        Browser set viewport 1440 1000 | Out-Null
    }
    Write-Output 'Light/dark responsive checks passed.'
    Evaluate 'window.settingsPreview.usageError=true' | Out-Null
    OpenSettings
    Browser wait --fn '!!document.querySelector(".usage-error")' | Out-Null
    Evaluate 'window.settingsPreview.usageError=false' | Out-Null
    Browser scrollintoview '.usage-error button' | Out-Null
    Browser click '.usage-error button' | Out-Null
    Browser wait --fn '!!document.querySelector(".settings-kv") && document.querySelector(".settings-kv").textContent.includes("80%")' | Out-Null
    Evaluate 'window.settingsPreview.encryption=false' | Out-Null
    OpenSettings
    Browser wait --fn '!!document.querySelector(".model-encryption-warning")' | Out-Null
    Check (Evaluate '[...document.querySelectorAll(".model-connection button[aria-label]")].filter(b=>/^(编辑连接|测试连接|重新测试|设为默认)：/.test(b.getAttribute("aria-label"))).every(b=>b.disabled)') 'Unavailable encryption must block credential-dependent actions.'
    Check (Evaluate '[...document.querySelectorAll(".model-actions button")].some(b=>b.textContent.includes("恢复平台默认")&&!b.disabled)') 'Recovery to platform must remain enabled.'
    Write-Output 'Settings, connection states, cursor, fixed navigation, usage retry and responsive checks passed.'
} catch {
    $failure = $_
    try { Write-Output (Evaluate '({url:location.href,text:document.body.innerText.slice(-1200)})' | ConvertTo-Json -Compress) } catch { }
    throw $failure
} finally { Browser close | Out-Null }
