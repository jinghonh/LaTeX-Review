/* Author-managed knowledge. No background extraction, no source edits, no AI. */
(() => {
  'use strict';
  let initialized = false;
  function start() {
    const workspace = window.reviewWorkspace;
    if (!workspace || initialized) return;
    initialized = true;
    const {element: el, button, api} = workspace;
    const panel = workspace.panels.knowledge;
    const host = el('div', undefined, {id: 'knowledge-manager'});
    panel.append(host);
    let current = null, selectedSource = '', units = [], pending = false, errorText = '', epoch = 0, candidatePrefill = null;
    const clone = value => JSON.parse(JSON.stringify(value));
    const id = prefix => `${prefix}-${crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(16).slice(2)}`;
    const canWrite = () => workspace.capability.available && workspace.capability.capabilities?.knowledge;
    const path = suffix => `knowledge${suffix || ''}${selectedSource ? '?source=' + selectedSource : ''}`;
    const request = body => api(path(), body === undefined ? undefined : {...body, expected_source: current?.source});
    const decisionRequest = body => api('decisions', {...body, expected_source: current?.source});
    const knowledgeUpdated = () => window.dispatchEvent(new CustomEvent('review-knowledge-updated'));
    function status(message) { errorText = message; const node = host.querySelector('[data-knowledge-status]'); if (node) node.textContent = message; }
    function input(form, label, value = '', type = 'text') {
      const wrapper = el('label', label + ' ');
      const field = el(type === 'textarea' ? 'textarea' : 'input', undefined, {'aria-label': label});
      if (type !== 'textarea') field.type = type;
      field.value = value;
      wrapper.append(field); form.append(wrapper, el('br'));
      return field;
    }
    function select(form, label, options, value) {
      const wrapper = el('label', label + ' '), field = el('select', undefined, {'aria-label': label});
      options.forEach(([key, text]) => field.append(el('option', text, {value: key})));
      if (value !== undefined) field.value = value;
      wrapper.append(field); form.append(wrapper, el('br'));
      return field;
    }
    const lines = value => value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    function scopeFields(form, value = {id: 'body', domain: 'body'}) {
      const name = input(form, '作用域标识', value.id);
      const domain = select(form, '作用域类型', ['project', 'abstract', 'body', 'supplement', 'section', 'theorem', 'proof', 'algorithm', 'local'].map(x => [x, x]), value.domain || 'local');
      const parent = input(form, '父作用域（可选）', value.parent_id || '');
      const node = input(form, '作用域节点（局部范围必填）', value.node_id || '');
      return () => {
        if (!name.value.trim()) throw new Error('请填写作用域标识');
        const scope = {id: name.value.trim(), domain: domain.value};
        if (workspace.report?.input?.entry) scope.entry = workspace.report.input.entry;
        if (parent.value.trim()) scope.parent_id = parent.value.trim();
        if (node.value.trim()) scope.node_id = node.value.trim();
        if (['section', 'theorem', 'proof', 'algorithm', 'local'].includes(scope.domain) && !scope.node_id) throw new Error('局部范围需要来自当前证据的节点标识，不能猜测偏移。');
        return scope;
      };
    }
    function evidenceChoices() {
      const choices = [...units];
      for (const finding of workspace.report?.findings || []) {
        for (const evidence of finding.evidence || []) choices.push({node_id: null, evidence: [evidence]});
      }
      const seen = new Set();
      return choices.filter(unit => unit.evidence?.length && unit.evidence.every(e => e.reliability === 'exact')).filter(unit => {
        const key = JSON.stringify(unit.evidence); if (seen.has(key)) return false; seen.add(key); return true;
      });
    }
    function evidenceField(form, existing = []) {
      const choices = evidenceChoices();
      const field = select(form, '当前原文证据', [['', '请选择可靠原文（不编造坐标）'], ...choices.map((unit, index) => [String(index), `${unit.evidence[0].file}:${unit.evidence[0].start_line} · ${unit.evidence.map(e => e.quote).join(' ').slice(0, 100)}`])]);
      const preview = el('pre'); form.append(preview);
      field.addEventListener('change', () => {
        const unit = choices[Number(field.value)];
        preview.textContent = field.value === '' ? '' : `节点：${unit.node_id || '无可靠节点'}\n` + unit.evidence.map(e => e.quote).join('\n');
        const scopeNode = form.querySelector('input[aria-label="作用域节点（局部范围必填）"]');
        const scopeDomain = form.querySelector('select[aria-label="作用域类型"]');
        if (field.value !== '' && unit.node_id && scopeNode && !scopeNode.value && !['project', 'abstract', 'body', 'supplement'].includes(scopeDomain?.value)) scopeNode.value = unit.node_id;
      });
      if (!choices.length) form.append(el('p', '没有可用证据。请检查当前论文，或刷新原文证据；不接受手工猜测坐标。'));
      if (existing.length) form.append(el('p', '已有证据保留；如正文已变，请选择当前证据重新确认。'));
      return () => field.value === '' ? clone(existing) : clone(choices[Number(field.value)].evidence);
    }
    function formSection(title) {
      const details = el('details'); details.append(el('summary', title));
      const form = el('form'); details.append(form); host.append(details);
      form.addEventListener('submit', event => event.preventDefault());
      return {form, details};
    }
    function submit(form, label, action) {
      const save = button(label, async () => {
        if (pending || !canWrite()) return;
        pending = true; save.disabled = true; status('正在保存；修订和内容哈希同时校验…');
        try { await action(); } catch (error) { status(error.message + '；发生冲突时请刷新，不会自动覆盖或合并。'); }
        finally { pending = false; save.disabled = !canWrite(); }
      });
      save.disabled = !canWrite(); form.append(save);
    }
    async function saveRecord(collection, record) {
      const registry = clone(current.registry);
      const index = registry[collection].findIndex(item => item.id === record.id);
      if (index < 0) registry[collection].push(record); else registry[collection][index] = record;
      const evidence = record.evidence || record.definitions || [];
      if (collection !== 'concepts' || evidence.length) {
        if (!workspace.report?.input_id || workspace.capability.stale) throw new Error('证据确认前请先检查当前论文；手工登记无证据概念不需要检查。');
        const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(evidence)));
        const fingerprint = Array.from(new Uint8Array(bytes)).map(x => x.toString(16).padStart(2, '0')).join('');
        current = await decisionRequest({collection, id: record.id, changes: record, action: collection === 'exceptions' ? 'waive' : 'confirm',
          reason: candidatePrefill && collection === 'relations' ? `作者确认语义候选 ${candidatePrefill.candidate.candidate_id || candidatePrefill.request_id}` : '作者通过知识表单确认',
          author: 'author', scope: record.scope || {id: 'project', domain: 'project'}, evidence_fingerprint: fingerprint,
          input_id: workspace.report.input_id, request_id: id('request'), expected_revision: current.revision, expected_hash: current.content_hash});
      } else current = await request({registry, expected_revision: current.revision, expected_hash: current.content_hash});
      knowledgeUpdated();
      if (collection === 'relations') candidatePrefill = null;
      errorText = '已保存知识；请复检论文。原文未改动。'; render();
    }
    function conceptForm(existing) {
      const {form, details} = formSection(existing ? `编辑概念：${existing.canonical_name}` : '登记概念 / 术语 / 缩写');
      const name = input(form, '正式名称', existing?.canonical_name || '');
      const type = input(form, '概念类型（可选）', existing?.type || '');
      const policyText = values => (values || []).map(x => typeof x === 'string' ? x : x.text).join('\n');
      const allowed = input(form, '允许名称（每行一个）', policyText(existing?.allowed_names), 'textarea');
      const forbidden = input(form, '禁用名称（每行一个）', policyText(existing?.forbidden_names), 'textarea');
      const code = input(form, '代码标识（每行一个）', policyText(existing?.code_identifiers), 'textarea');
      const contexts = input(form, '检查上下文（逗号分隔）', (existing?.contexts || ['body', 'abstract', 'caption']).join(','));
      form.append(el('p', '上下文：body / abstract / caption / title / table / texttt；代码映射仅在显式上下文内检查。'));
      const sensitive = select(form, '大小写策略', [['yes', '区分大小写'], ['no', '不区分大小写']], existing?.case_sensitive === false ? 'no' : 'yes');
      const acronym = input(form, '缩写（可选）', existing?.acronym?.short || '');
      const expansion = input(form, '缩写全称（可选）', existing?.acronym?.expansion || '');
      const scope = scopeFields(form, existing?.scope || {id: 'project', domain: 'project'});
      const evidence = evidenceField(form, existing?.definitions || []);
      submit(form, existing ? '保存概念' : '登记概念', async () => {
        if (!name.value.trim()) throw new Error('正式名称不能为空');
        if (!!acronym.value.trim() !== !!expansion.value.trim()) throw new Error('缩写与全称必须一起填写');
        const selectedContexts = contexts.value.split(',').map(x => x.trim()).filter(Boolean);
        const record = {...existing, id: existing?.id || id('concept'), canonical_name: name.value.trim(), source: 'manual', status: 'confirmed',
          allowed_names: lines(allowed.value).map(text => clone(existing?.allowed_names?.find(x => (typeof x === 'string' ? x : x.text) === text) || text)),
          forbidden_names: lines(forbidden.value).map(text => clone(existing?.forbidden_names?.find(x => (typeof x === 'string' ? x : x.text) === text) || text)),
          code_identifiers: lines(code.value).map(text => ({text, contexts: selectedContexts})),
          contexts: selectedContexts, case_sensitive: sensitive.value === 'yes', scope: scope(), definitions: evidence()};
        if (type.value.trim()) record.type = type.value.trim(); else delete record.type;
        if (acronym.value.trim()) record.acronym = {short: acronym.value.trim(), expansion: expansion.value.trim()}; else delete record.acronym;
        await saveRecord('concepts', record);
      });
      if (existing) details.dataset.conceptId = existing.id;
    }
    function roleForm() {
      const {form} = formSection('登记出现角色：预告 / 定义 / 正式使用');
      const targets = [...current.registry.concepts, ...current.registry.symbol_bindings];
      const target = select(form, '对应概念或符号', targets.map(x => [x.id, x.canonical_name || x.symbol]));
      const role = select(form, '出现角色', [['preview', '预告'], ['definition', '定义'], ['formal_use', '正式使用'], ['mention', '一般提及'], ['example', '示例'], ['quoted', '引用'], ['unknown', '未知（不作事实）']]);
      const scope = scopeFields(form);
      const group = input(form, '同一引入组（公式后解释可共用，可选）');
      const evidence = evidenceField(form);
      submit(form, '确认出现角色', async () => {
        const selected = evidence();
        if (!target.value || !selected.length) throw new Error('角色确认需要对应对象与可靠原文证据');
        const record = {id: id('occurrence'), target_id: target.value, role: role.value, entry: selected[0].entry,
          scope: scope(), source: 'author_confirmed', status: 'confirmed', evidence: selected};
        if (group.value.trim()) record.introduction_group = group.value.trim();
        await saveRecord('occurrences', record);
      });
    }
    function symbolForm() {
      const {form} = formSection('登记符号绑定（不推断复杂公式类型）');
      const symbol = input(form, '符号 LaTeX');
      const meaning = input(form, '确认含义');
      const type = input(form, '简单类型（可选）');
      const dimensions = input(form, '维度描述（可选，不自动推理）');
      const shadows = select(form, '显式遮蔽的外层绑定（可选）', [['', '无显式遮蔽'], ...current.registry.symbol_bindings.map(x => [x.id, `${x.symbol}：${x.meaning}`])]);
      const incompatible = select(form, '明确不兼容的已有绑定（可选）', [['', '未声明不兼容'], ...current.registry.symbol_bindings.map(x => [x.id, `${x.symbol}：${x.meaning}`])]);
      const scope = scopeFields(form);
      const evidence = evidenceField(form);
      submit(form, '登记符号', async () => {
        if (!symbol.value.trim() || !meaning.value.trim() || !evidence().length) throw new Error('符号、含义和原文证据均必填');
        const record = {id: id('binding'), symbol: symbol.value.trim(), meaning: meaning.value.trim(),
          scope: scope(), evidence: evidence(), source: 'manual', status: 'confirmed'};
        if (type.value.trim()) record.type = type.value.trim();
        if (dimensions.value.trim()) record.dimensions = dimensions.value.trim();
        if (shadows.value && shadows.value === incompatible.value) throw new Error('同一绑定不能同时声明为合法遮蔽和不兼容。');
        if (shadows.value) record.shadows = shadows.value;
        if (incompatible.value) record.incompatible_with = [incompatible.value];
        await saveRecord('symbol_bindings', record);
      });
    }
    function relationForm() {
      const {form, details} = formSection('确认概念关系（只有强依赖参与顺序检查）');
      if (candidatePrefill) { details.open = true; form.append(el('p', 'AI 仅预填候选，须作者逐项确认后保存：' + candidatePrefill.candidate.hypothesis)); }
      const choices = current.registry.concepts.map(x => [x.id, x.canonical_name]);
      const from = select(form, '起点概念', choices), to = select(form, '终点概念', choices);
      const kind = select(form, '关系类型', [['depends_on', '起点依赖终点'], ['same_concept', '同一概念'], ['different_concept', '不同概念'], ['code_mapping', '代码映射']]);
      const strength = select(form, '依赖强度', [['strong', '强：定义依赖'], ['weak', '弱：仅候选提示']]);
      const evidence = evidenceField(form, candidatePrefill?.candidate.sources || []);
      if (candidatePrefill) {
        from.value = candidatePrefill.candidate.object_ids[0]; to.value = candidatePrefill.candidate.object_ids[1];
        kind.value = candidatePrefill.candidate.kind;
        strength.value = 'weak'; // A candidate never becomes a strong dependency by default.
        form.append(button('查看候选原文证据', event => workspace.showEvidence({message: candidatePrefill.candidate.hypothesis, evidence: candidatePrefill.candidate.sources}, event.currentTarget)));
      }
      submit(form, '确认关系', async () => {
        if (!from.value || !to.value || from.value === to.value || !evidence().length) throw new Error('需要两个不同概念及可靠证据');
        if (candidatePrefill && candidatePrefill.input_id !== workspace.report?.input_id) throw new Error('候选来自旧检查版本，请重新生成候选，不能以新版本冒充旧证据。');
        await saveRecord('relations', {id: id('relation'), kind: kind.value, from_id: from.value, to_id: to.value,
          strength: strength.value, evidence: evidence(), status: 'confirmed', source: 'author_confirmed'});
      });
    }
    function exceptionForm() {
      const {form} = formSection('为当前问题设置局部例外（不把问题标成已解决）');
      const findings = workspace.report?.findings || [];
      const finding = select(form, '当前问题', findings.map((x, i) => [String(i), x.message || x.rule_id]));
      const reason = input(form, '例外原因');
      const scope = scopeFields(form, {id: 'project', domain: 'project'});
      submit(form, '确认局部例外', async () => {
        const selected = findings[Number(finding.value)];
        if (!selected || !reason.value.trim()) throw new Error('请先运行检查，选择问题并填写原因');
        await saveRecord('exceptions', {id: id('exception'), rule_id: selected.rule_id, finding_key: selected.finding_key,
          evidence: clone(selected.evidence), scope: scope(), reason: reason.value.trim(), status: 'active',
          valid_when: {rule_version: selected.rule_version}, invalid_when: ['evidence_changed', 'rule_changed', 'scope_changed']});
      });
    }
    function transferTools() {
      const {form} = formSection('导入 / 导出 / 恢复 / 高级编辑');
      form.append(button('导出当前权威知识 JSON', () => {
        const url = URL.createObjectURL(new Blob([JSON.stringify(current.registry, null, 2)], {type: 'application/json'}));
        const link = el('a', 'knowledge.json', {href: url, download: 'knowledge.json'}); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      }));
      const content = input(form, '待导入 JSON（先预览冲突）', '', 'textarea');
      const preview = el('div'); form.append(preview);
      submit(form, '预览导入冲突', async () => {
        const incoming = JSON.parse(content.value);
        const result = await request({operation: 'import_preview', registry: incoming});
        preview.replaceChildren(el('p', `新增 ${result.additions.length} · 相同 ${result.identical.length} · 冲突 ${result.conflicts.length}；不会静默合并。`));
        const choices = [];
        result.conflicts.forEach(conflict => {
          preview.append(el('pre', JSON.stringify(conflict, null, 2)));
          const choice = select(preview, `${conflict.collection}/${conflict.id}`, [['', '必须明确选择'], ['current', '保留当前'], ['incoming', '使用导入']]);
          choices.push([`${conflict.collection}/${conflict.id}`, choice]);
        });
        preview.append(button('按明确选择导入', async () => {
          try {
            if (choices.some(([, field]) => !field.value)) throw new Error('每个冲突都必须选择解决方式');
            current = await request({operation: 'import', registry: incoming, resolutions: Object.fromEntries(choices.map(([key, field]) => [key, field.value])),
              expected_revision: result.revision, expected_hash: result.content_hash});
            knowledgeUpdated(); errorText = '已导入；来源证据仍需复核，锚点不会被假定为当前事实。'; render();
          } catch (error) { status(error.message); }
        }));
      });
      const advanced = input(form, '高级权威 JSON（严格 Schema 校验；普通登记请用上方表单）', JSON.stringify(current.registry, null, 2), 'textarea');
      submit(form, '条件保存高级 JSON', async () => {
        current = await request({registry: JSON.parse(advanced.value), expected_revision: current.revision, expected_hash: current.content_hash});
        knowledgeUpdated(); errorText = '高级编辑已保存；请复检。'; render();
      });
    }
    function render() {
      host.replaceChildren(el('h3', '作者确认的知识注册表'));
      const notice = el('p', errorText || '基础检查不会自动建库；下面的显式保存才写知识文件。', {'data-knowledge-status': '', role: 'status'}); host.append(notice);
      if (!canWrite()) { host.append(el('p', '静态 / 历史报告只读：不能读取或修改活动知识，也不会创建知识文件。')); return; }
      const toolbar = el('div'); host.append(toolbar);
      const source = select(toolbar, '权威知识来源', [['', '自动（冲突时必须明确选择）'], ['project', '论文项目知识文件'], ['external', '外部持久知识文件（只读论文）']], selectedSource);
      source.addEventListener('change', () => { selectedSource = source.value; current = null; load(); });
      toolbar.append(button('刷新知识与原文证据', load));
      if (!current) { host.append(el('p', '尚未读取知识；选择一个明确来源或刷新。')); return; }
      host.append(el('p', `来源 ${current.source} · 修订 ${current.revision} · ${current.status} · ${current.path}`));
      if (current.alternatives?.length) host.append(el('p', '另一个知识来源存在但未合并：' + current.alternatives.join('；')));
      if (current.status === 'corrupt') {
        host.append(el('p', '知识损坏，原始字节保留。禁止把空注册表覆盖回去。'));
        host.append(button('保留损坏原件并恢复有效备份', async () => {
          try { current = await request({operation: 'recover', expected_revision: current.revision, expected_hash: current.content_hash}); knowledgeUpdated(); errorText = '已恢复备份；损坏原件已保留。'; render(); }
          catch (error) { status(error.message); }
        })); return;
      }
      conceptForm(); symbolForm(); roleForm(); relationForm(); exceptionForm();
      host.append(el('h3', `概念 ${current.registry.concepts.length} · 符号 ${current.registry.symbol_bindings.length} · 角色 ${current.registry.occurrences.length} · 关系 ${current.registry.relations.length}`));
      current.registry.concepts.forEach(conceptForm);
      for (const collection of ['symbol_bindings', 'occurrences', 'relations', 'exceptions']) {
        current.registry[collection].forEach(record => {
          const row = el('details'); row.append(el('summary', `${collection} · ${record.symbol || record.role || record.kind || record.reason || record.id}`), el('pre', JSON.stringify(record, null, 2)));
          row.append(button('删除此记录（可撤销审计决定）', async () => {
            try {
              if (!workspace.report?.input_id) throw new Error('请先检查当前版本，再提交删除决定');
              current = await decisionRequest({collection, id: record.id, changes: null, action: 'edit', reason: '作者显式删除登记记录',
                author: 'author', scope: record.scope || {id: 'project', domain: 'project'}, evidence_fingerprint: current.content_hash,
                input_id: workspace.report.input_id, request_id: id('request'), expected_revision: current.revision, expected_hash: current.content_hash});
              knowledgeUpdated(); errorText = '记录已删除；可在决定历史中撤销。请复检。'; render(); }
            catch (error) { status(error.message); }
          })); host.append(row);
        });
      }
      if (current.registry.decisions.length) {
        const audit = el('details'); audit.append(el('summary', '作者决定与可逆操作历史')); host.append(audit);
        current.registry.decisions.slice().reverse().forEach(decision => {
          const row = el('div'); row.append(el('p', `${decision.action} · ${decision.reason} · ${decision.author}${decision.imported ? ' · 导入历史（只读，需重新确认当前证据）' : ''}`));
          if (!decision.imported) row.append(button('撤销该决定（保留审计记录）', async () => {
            try { current = await request({operation: 'undo', decision_id: decision.id, expected_revision: current.revision, expected_hash: current.content_hash}); knowledgeUpdated(); errorText = '已追加撤销决定；请复检。'; render(); }
            catch (error) { status(error.message); }
          })); audit.append(row);
        });
      }
      transferTools();
    }
    async function load() {
      const token = ++epoch;
      if (!canWrite()) { current = null; units = []; candidatePrefill = null; render(); return; }
      try {
        const result = await request();
        if (token !== epoch) return;
        current = result; errorText = '';
        try {
          let cursor = '0', collected = [];
          do {
            const route = path('/evidence') + (selectedSource ? '&' : '?') + 'limit=100&cursor=' + encodeURIComponent(cursor);
            const evidence = await api(route);
            if (token !== epoch) return;
            collected.push(...(evidence.units || [])); cursor = evidence.next_cursor;
          } while (cursor !== null && cursor !== undefined);
          units = collected;
        } catch (_) { units = []; } // Older service: only immutable check findings provide evidence.
      } catch (error) { if (token === epoch) { current = null; errorText = error.message + '。请选择单一权威来源，不能静默合并。'; } }
      if (token === epoch) render();
    }
    window.addEventListener('review-semantic-confirm-relation', event => {
      const detail = event.detail, candidate = detail?.candidate;
      const ids = candidate?.object_ids || [];
      if (!current || !candidate || !['same_concept', 'different_concept', 'code_mapping'].includes(candidate.kind)
          || ids.length !== 2 || ids[0] === ids[1] || ids.some(key => !current.registry.concepts.some(x => x.id === key))
          || !candidate.sources?.length || detail.input_id !== workspace.report?.input_id) {
        status('候选关系缺少两个已登记概念、当前证据或版本不匹配；不会猜测对象或自动导入。'); return;
      }
      candidatePrefill = clone(detail); render();
    });
    window.addEventListener('review-workspace-capabilities', load);
    window.addEventListener('review-checks-updated', () => { if (!pending) load(); else if (!canWrite()) render(); });
    render();
    if (canWrite()) load();
  }
  window.addEventListener('review-workspace-ready', start);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
