/* Polling updates existing nodes so images, focus and log scroll positions survive. */
(() => {
  const rendered = new WeakMap();
  const key = node => node.nodeType === 1
    ? ['id', 'data-account', 'data-platform', 'data-task', 'data-target'].map(name => node.getAttribute(name) || '').join('|')
    : '';
  const keyed = node => key(node).replaceAll('|', '') !== '';
  const compatible = (a, b) => a.nodeType === b.nodeType && a.nodeName === b.nodeName && key(a) === key(b);
  function patch(parent, incoming) {
    let current = parent.firstChild;
    for (const next of [...incoming.childNodes]) {
      if (!current || !compatible(current, next)) {
        const existing = keyed(next) && [...parent.childNodes].find(node => compatible(node, next));
        if (existing) parent.insertBefore(existing, current);
        else parent.insertBefore(next.cloneNode(true), current);
        current = current ? current.previousSibling : parent.lastChild;
      }
      if (current.nodeType === 1) {
        for (const attr of [...current.attributes]) if (!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
        for (const attr of next.attributes) if (current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
        patch(current, next);
        if (current instanceof HTMLInputElement && ['checkbox', 'radio'].includes(current.type)) current.checked = next.checked;
        if (current instanceof HTMLOptionElement) current.selected = next.selected;
      } else if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
      current = current.nextSibling;
    }
    while (current) { const next = current.nextSibling; current.remove(); current = next; }
  }
  window.renderStable = (element, html) => {
    if (rendered.get(element) === html) return false;
    const template = document.createElement('template');
    template.innerHTML = html;
    patch(element, template.content);
    rendered.set(element, html);
    return true;
  };
  window.setStableText = (element, value) => {
    const text = String(value);
    if (element.textContent !== text) element.textContent = text;
  };
})();
