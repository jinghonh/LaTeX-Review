"""The complete DOM remains searchable while formula tasks yield and finish."""
from pathlib import Path
from test_annotation_delivery_browser import chrome


def document(count=120):
    script=Path(__file__).parents[1].joinpath('src/latex_review/math_render.js').read_text()
    return '<body><script>window.print=()=>window.printed=document.querySelectorAll(".math-tex mjx-container").length</script><p id="math-status"></p>'+''.join(f'<span class="math-tex">x_{i}</span>' for i in range(count))+'<script>'+script+'</script></body>'


def test_batches_dynamic_source_translation_and_print(chrome):
    page=chrome.new_page()
    page.add_init_script('window.print=()=>window.printed=document.querySelectorAll(".math-tex mjx-container").length')
    page.goto('about:blank')
    page.set_content(document())
    page.evaluate('''()=>{window.MathJax={startup:{promise:Promise.resolve()},tex2chtmlPromise:async latex=>{const start=performance.now();while(performance.now()-start<2){}const node=document.createElement('mjx-container');node.textContent=latex;return node;}};window.heartbeat=0;setInterval(()=>heartbeat++,1);mathDependencyReady();}''')
    assert page.locator('.math-tex').count()==120
    page.wait_for_function("document.querySelector('#math-status').dataset.state==='ready'")
    assert page.evaluate('heartbeat')>4
    assert page.locator('.math-tex mjx-container').count()==120
    page.evaluate('''()=>{const node=document.querySelector('.math-tex');node.textContent='changed';const translated=document.createElement('span');translated.className='math-tex';translated.textContent='translated';document.body.append(translated);}''')
    page.wait_for_function("document.querySelector('.math-tex mjx-container').textContent==='changed'&&document.querySelectorAll('.math-tex mjx-container').length===121")
    result=page.evaluate('reviewMath.complete()');assert result['total']==121 and result['errors']==0
    page.evaluate('window.print()')
    assert page.evaluate('printed')==121
    assert page.pdf().startswith(b'%PDF')
    page.close()


def test_offline_keeps_source_and_completes(chrome):
    page=chrome.new_page();page.set_content(document(80));page.evaluate('mathDependencyFailed()')
    result=page.evaluate('reviewMath.complete()')
    assert result['failed'] and result['errors']==80
    assert page.locator('.math-tex').last.text_content()=='x_79'
    assert page.locator('.math-tex.preview-unavailable').count()==80
    assert '保留公式原文' in page.locator('#math-status').text_content()
    page.close()


def test_native_print_preserves_pending_source_and_warns(chrome):
    page=chrome.new_page();page.set_content(document(40))
    page.evaluate('window.dispatchEvent(new Event("beforeprint"))')
    assert page.locator('.review-print-warning').is_visible()
    assert page.locator('.math-tex').last.text_content()=='x_39'
    page.evaluate('window.dispatchEvent(new Event("afterprint"))')
    assert page.locator('.review-print-warning').count()==0
    page.close()


def test_source_changes_while_math_extension_is_loading(chrome):
    page=chrome.new_page();page.set_content(document(1))
    page.evaluate("""()=>{window.MathJax={startup:{promise:Promise.resolve()},tex2chtmlPromise:async latex=>{if(!window.released)await new Promise(resolve=>window.release=()=>{window.released=true;resolve();});const n=document.createElement('mjx-container');n.textContent=latex;return n;}};mathDependencyReady();}""")
    page.wait_for_function('Boolean(window.release)')
    page.evaluate("document.querySelector('.math-tex').textContent='latest';release()")
    page.evaluate('reviewMath.complete()')
    assert page.locator('.math-tex mjx-container').text_content()=='latest'
    page.close()
