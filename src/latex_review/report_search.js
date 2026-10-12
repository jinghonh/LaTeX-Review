(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('reader-data').textContent);
  const normalize = text => text.toLocaleLowerCase();
  const items = [];
  data.nodes.forEach(item => {
    const target = document.getElementById(item.side + '-' + item.id);
    if (!target?.classList.contains('review-node')) return;
    const copy = target.cloneNode(true);
    copy.querySelectorAll('.review-node,.change-anchor,.inline-change,.change-review-editor,.translation-block,.deletion-marker,.change-trigger,.annotation-card,.annotation-toolbar,.annotation-mark,.translation-item-controls,button,input,select,textarea').forEach(node => {
      if (node.classList.contains('change-trigger') || node.classList.contains('annotation-mark'))
        node.replaceWith(...node.childNodes);
      else node.remove();
    });
    items.push({...item, target, sectionKey: JSON.stringify(item.section),
      text: (copy.textContent || '') + ' ' + (item.citations || []).join(' ')});
  });
  window.reviewSearch = Object.freeze({
    items,
    find(query, sectionKey = 'all') {
      query = normalize(query.trim());
      return items.flatMap(item => {
        if (sectionKey !== 'all' && sectionKey !== item.sectionKey) return [];
        const translatedText = Array.from(item.target.querySelectorAll('.translation-block'))
          .filter(block => block.closest('.review-node') === item.target)
          .map(block => {
            const copy = block.cloneNode(true);
            copy.querySelectorAll('.translation-label,.annotation-card,.review-node,button,input,select,textarea')
              .forEach(node => node.remove());
            return copy.textContent;
          }).join(' ');
        const originalMatch = !query || normalize(item.text).includes(query);
        if (!originalMatch && !normalize(translatedText).includes(query)) return [];
        const text = originalMatch ? item.text : translatedText;
        const index = query ? normalize(text).indexOf(query) : 0;
        const start = Math.max(0, index - 35);
        return [{...item, translated: !originalMatch, snippet: text.slice(start, start + 160).trim()}];
      });
    }
  });
})();
