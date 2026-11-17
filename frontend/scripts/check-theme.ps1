# Run against the local app; uses an isolated guest browser and never writes account data.
param([string]$BaseUrl = 'http://127.0.0.1:5173')
$ErrorActionPreference = 'Stop'
$themeSession = 'theme-' + [guid]::NewGuid().ToString('N')
$browserExecutable = Join-Path (Split-Path (Get-Command agent-browser).Source) 'node_modules/agent-browser/bin/agent-browser-win32-x64.exe'
function Browser {
    $start = [Diagnostics.ProcessStartInfo]::new($browserExecutable)
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in @('--session', $themeSession, '--json') + $args) { $start.ArgumentList.Add([string]$argument) }
    $process = [Diagnostics.Process]::Start($start)
    $line = $process.StandardOutput.ReadLine()
    if (-not $process.WaitForExit(30000)) { $process.Kill(); throw 'Browser command timed out.' }
    $process.Dispose()
    $reply = $line | ConvertFrom-Json
    if (-not $reply.success) { throw $reply.error }
    return $reply.data
}
function Evaluate([string]$Code) {
    return (Browser eval -b ([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Code)))).result
}
function Check([bool]$Pass, [string]$Message) { if (-not $Pass) { throw $Message } }
$contrastHelpers = @'
window.themeContrast = (foreground, background) => {
    const context = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
    const luminance = (color) => {
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = color;
        context.fillRect(0, 0, 1, 1);
        const channels = [...context.getImageData(0, 0, 1, 1).data].slice(0, 3).map(v => {
            const c = v / 255;
            return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
        });
        return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
    };
    const a = luminance(foreground), b = luminance(background);
    return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
};
'@
try {
    Browser set viewport 1440 1000 | Out-Null
    Browser open "$BaseUrl/" | Out-Null
    Browser wait --fn '!!document.querySelector(".home-panel-inner")' | Out-Null
    foreach ($theme in @('light', 'dark')) {
        Browser set media $theme | Out-Null
        Evaluate "localStorage.setItem('knowloop-theme', '$theme')" | Out-Null
        Browser open "$BaseUrl/" | Out-Null
        Browser wait --fn '!!document.querySelector(".home-panel-inner")' | Out-Null
        Check ((Evaluate 'document.documentElement.dataset.theme') -eq $theme) "$theme selection must survive reload."
        Evaluate $contrastHelpers | Out-Null
        $palette = Evaluate @'
(() => {
    const probe = document.body.appendChild(document.createElement('span'));
    const rows = [];
    for (const surface of ['--background', '--surface', '--surface-raised', '--surface-muted']) {
        probe.style.backgroundColor = `var(${surface})`;
        for (const text of ['--foreground', '--text-secondary', '--text-muted', '--success', '--warning', '--danger', '--info']) {
            probe.style.color = `var(${text})`;
            const style = getComputedStyle(probe);
            rows.push({ name: `${text} on ${surface}`, minimum: 4.5, ratio: window.themeContrast(style.color, style.backgroundColor) });
        }
        for (const control of ['--border-strong', '--control-track', '--control-track-hover', '--control-on', '--focus-outline']) {
            probe.style.color = `var(${control})`;
            const style = getComputedStyle(probe);
            rows.push({ name: `${control} on ${surface}`, minimum: 3, ratio: window.themeContrast(style.color, style.backgroundColor) });
        }
    }
    probe.remove();
    return rows;
})()
'@
        foreach ($pair in $palette) { Check ($pair.ratio -ge $pair.minimum) "$theme $($pair.name): $($pair.ratio.ToString('F2')):1, expected $($pair.minimum):1." }
        Browser set viewport 390 1000 | Out-Null
        if (Evaluate 'document.querySelector(".nav-toggle").getAttribute("aria-expanded") === "true"') {
            Browser find role button click --name '收起导航' --exact | Out-Null
        }
        if (Evaluate 'document.querySelector(".overview-toggle").getAttribute("aria-expanded") === "true"') {
            Browser find role button click --name '收起资料概况' --exact | Out-Null
        }
        Browser wait --fn 'Math.abs(document.querySelector(".home-panel").getBoundingClientRect().width - 36) < 1' | Out-Null
        Check (Evaluate '(()=>{const panel=document.querySelector(".home-panel").getBoundingClientRect();const composer=document.querySelector(".composer").getBoundingClientRect();return panel.left>=composer.right && panel.right<=innerWidth})()') "$theme collapsed mobile overview must not cover the composer."
        Browser set viewport 1440 1000 | Out-Null
        Browser find role button click --name '展开导航' --exact | Out-Null
        Browser find role button click --name '登录' --exact | Out-Null
        Browser wait --fn '!!document.querySelector("input[name=password]")' | Out-Null
        $field = Evaluate @'
(() => {
    const input = document.querySelector('input[name=email]');
    const style = getComputedStyle(input);
    const dialog = getComputedStyle(document.querySelector('[role=dialog]'));
    return {
        placeholder: window.themeContrast(getComputedStyle(input, '::placeholder').color, style.backgroundColor),
        border: window.themeContrast(style.borderTopColor, style.backgroundColor),
        raised: (() => {
            const probe = document.body.appendChild(document.createElement('span'));
            probe.style.background = 'var(--surface-raised)';
            const same = getComputedStyle(probe).backgroundColor === dialog.backgroundColor;
            probe.remove();
            return same;
        })()
    };
})()
'@
        Check ($field.placeholder -ge 4.5) "$theme login placeholder must reach 4.5:1."
        Check ($field.border -ge 3) "$theme input boundary must reach 3:1."
        Check $field.raised "$theme account dialog must use the shared raised surface."
        Browser press Escape | Out-Null
        Browser press Tab | Out-Null
        $focus = Evaluate @'
(() => {
    const style = getComputedStyle(document.activeElement);
    return {
        visible: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2,
        ratio: window.themeContrast(style.outlineColor, getComputedStyle(document.body).backgroundColor)
    };
})()
'@
        Check ($focus.visible -and $focus.ratio -ge 3) "$theme keyboard focus must remain visible at 3:1."
        Browser open "$BaseUrl/chat/demo" | Out-Null
        Browser wait --fn '!!document.querySelector("code.hljs .hljs-string")' | Out-Null
        Evaluate $contrastHelpers | Out-Null
        $code = Evaluate @'
[...document.querySelectorAll('code.hljs, code.hljs span')].map(node => ({
    name: node.className,
    ratio: window.themeContrast(getComputedStyle(node).color, getComputedStyle(node.closest('pre')).backgroundColor)
}))
'@
        foreach ($pair in $code) { Check ($pair.ratio -ge 4.5) "$theme code $($pair.name) must reach 4.5:1." }
        foreach ($width in @(390, 768, 1440)) {
            Browser set viewport $width 1000 | Out-Null
            Check (Evaluate 'document.documentElement.scrollWidth <= innerWidth') "$theme chat overflows at $width px."
        }
        Write-Output "$theme palette, account dialog, code and responsive checks passed."
    }
    Evaluate "localStorage.setItem('knowloop-theme', 'system')" | Out-Null
    Browser open "$BaseUrl/" | Out-Null
    Browser wait --fn '!!document.querySelector(".home-panel-inner")' | Out-Null
    foreach ($theme in @('light', 'dark')) {
        Browser set media $theme | Out-Null
        Browser wait --fn "document.documentElement.dataset.theme === '$theme'" | Out-Null
        Check ((Evaluate "localStorage.getItem('knowloop-theme')") -eq 'system') 'System changes must preserve the preference.'
    }
    Write-Output 'Persisted selection and live system following checks passed.'
} finally { Browser close | Out-Null }
