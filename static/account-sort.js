/* 账号列表排序：指针跟随、占位让位、取消与边缘滚动。 */
(() => {
  window.createAccountSorter = (list, {onStart, onCommit} = {}) => {
    let drag = null;
    let cancelledPointer = null;
    let ignoreClickUntil = 0;
    const animations = new Map();
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

    const items = current => [...current.parent.children].filter(node =>
      node !== current.source && node.matches(current.selector));
    const layoutRect = node => {
      const rect = node.getBoundingClientRect();
      const transform = getComputedStyle(node).transform;
      const matrix = transform === 'none' ? null : new DOMMatrixReadOnly(transform);
      return {left: rect.left - (matrix?.m41 || 0), top: rect.top - (matrix?.m42 || 0),
        width: rect.width, height: rect.height, right: rect.right - (matrix?.m41 || 0), bottom: rect.bottom - (matrix?.m42 || 0)};
    };
    const animateLayout = (current, change) => {
      const before = new Map(items(current).map(node => [node, node.getBoundingClientRect()]));
      animations.forEach(animation => animation.cancel());
      animations.clear();
      change();
      if (reducedMotion.matches) return;
      before.forEach((old, node) => {
        const now = layoutRect(node);
        const distance = old.top - now.top;
        if (Math.abs(distance) < 1) return;
        const animation = node.animate([{transform: `translateY(${distance}px)`}, {transform: 'translateY(0)'}],
          {duration: 180, easing: 'cubic-bezier(.2,.8,.2,1)'});
        animations.set(node, animation);
        animation.finished.then(() => {
          if (animations.get(node) === animation) animations.delete(node);
        }, () => {});
      });
    };

    const scrollHost = node => {
      for (let parent = node; parent && parent !== document.body; parent = parent.parentElement) {
        if (/(auto|scroll)/.test(getComputedStyle(parent).overflowY) && parent.scrollHeight > parent.clientHeight + 2) return parent;
      }
      return null;
    };
    const autoScroll = current => {
      const bounds = current.parent.getBoundingClientRect();
      if (current.x < bounds.left - 32 || current.x > bounds.right + 32) return 0;
      const host = current.scrollHost;
      const rect = host ? host.getBoundingClientRect() : {top: 0, bottom: innerHeight};
      const edge = 42;
      const speed = current.y < rect.top + edge ? -Math.min(12, (rect.top + edge - current.y) / 3)
        : current.y > rect.bottom - edge ? Math.min(12, (current.y - rect.bottom + edge) / 3) : 0;
      if (!speed) return 0;
      const before = host ? host.scrollTop : scrollY;
      if (host) host.scrollTop += speed;
      else window.scrollBy(0, speed);
      return (host ? host.scrollTop : scrollY) - before;
    };
    const isInside = current => {
      const rect = current.parent.getBoundingClientRect();
      return current.x >= rect.left - 24 && current.x <= rect.right + 24 &&
        current.y >= rect.top - 36 && current.y <= rect.bottom + 36;
    };
    const positionGhost = current => {
      current.ghost.style.transform = `translate3d(${current.x - current.offsetX}px,${current.y - current.offsetY}px,0) scale(1.025)`;
    };
    const moveSlot = current => {
      current.valid = isInside(current);
      current.ghost.classList.toggle('is-invalid', !current.valid);
      current.feedback.textContent = current.valid ? '松开放置 · Esc 取消' : '拖回列表放置 · 松开取消';
      if (!current.valid || !current.direction) return;
      const sequence = [...current.parent.children].filter(node => node === current.slot ||
        (node !== current.source && node.matches(current.selector)));
      const at = sequence.indexOf(current.slot);
      let reference;
      const step = current.direction;
      for (let index = at + step; index >= 0 && index < sequence.length; index += step) {
        const candidate = sequence[index];
        const anchor = current.kind !== 'account' ? candidate.querySelector(':scope > summary') : candidate;
        const rect = layoutRect(anchor);
        // 平台标题的父分组可能正在让位；扣掉父级动画的位移。
        const parentTransform = current.kind !== 'account' ? getComputedStyle(candidate).transform : 'none';
        const offset = parentTransform === 'none' ? 0 : new DOMMatrixReadOnly(parentTransform).m42;
        const midpoint = rect.top - offset + rect.height / 2;
        if (step > 0 ? current.y > midpoint + 6 : current.y < midpoint - 6) {
          reference = step > 0 ? candidate.nextSibling : candidate;
        } else break;
      }
      if (reference !== undefined && reference !== current.slot) {
        animateLayout(current, () => current.parent.insertBefore(current.slot, reference));
      }
    };
    const tick = current => {
      if (drag !== current || current.phase !== 'sorting') return;
      positionGhost(current);
      const scrolling = autoScroll(current);
      if (scrolling) current.direction = Math.sign(scrolling);
      moveSlot(current);
      current.frame = requestAnimationFrame(() => tick(current));
    };

    const lift = current => {
      if (drag !== current || !current.source.isConnected) return;
      clearTimeout(current.timer);
      current.phase = 'sorting';
      current.source.classList.remove('sort-pressing');
      onStart?.();
      const rect = current.source.getBoundingClientRect();
      const style = getComputedStyle(current.source);
      current.offsetX = current.startX - rect.left;
      current.offsetY = current.startY - rect.top;
      current.initialOrder = [...current.parent.children].filter(node => node.matches(current.selector));
      current.scrollHost = scrollHost(current.parent);

      const slot = document.createElement('div');
      slot.className = `sort-placeholder ${current.kind}-slot`;
      slot.setAttribute('aria-hidden', 'true');
      slot.style.height = `${rect.height}px`;
      slot.style.margin = `${style.marginTop} ${style.marginRight} ${style.marginBottom} ${style.marginLeft}`;
      const slotLabel = document.createElement('span');
      slotLabel.textContent = current.kind === 'category' ? '放置作品分类' : current.kind === 'platform' ? '放置平台分组' : '放置账号';
      slot.append(slotLabel);
      current.source.before(slot);
      current.slot = slot;

      const ghost = document.createElement('div');
      ghost.className = `workspace-sidebar sort-ghost ${current.kind}-ghost`;
      ghost.style.width = `${rect.width}px`;
      ghost.setAttribute('aria-hidden', 'true');
      const clone = current.source.cloneNode(true);
      clone.classList.remove('sort-pressing');
      if (current.kind !== 'account') clone.querySelector('.platform-accounts')?.remove();
      for (const node of [clone, ...clone.querySelectorAll('*')]) {
        for (const attribute of [...node.attributes]) {
          if (attribute.name === 'id' || attribute.name === 'title' || attribute.name.startsWith('on') || attribute.name.startsWith('data-')) node.removeAttribute(attribute.name);
        }
        if (node.matches('button,summary,a,input,select')) node.setAttribute('tabindex', '-1');
      }
      const feedback = document.createElement('div');
      feedback.className = 'sort-feedback';
      feedback.textContent = '松开放置 · Esc 取消';
      ghost.append(clone, feedback);
      document.body.append(ghost);
      current.ghost = ghost;
      current.feedback = feedback;
      current.source.classList.add('sort-source');
      document.body.classList.add('account-sorting');
      list.dataset.sorting = current.kind;
      try { list.setPointerCapture(current.pointerId); } catch (_) {}
      positionGhost(current);
      if (!reducedMotion.matches) ghost.animate([{opacity: .35}, {opacity: 1}], {duration: 110});
      tick(current);
    };

    const end = (commit = false) => {
      const current = drag;
      if (!current || current.phase === 'settling') return;
      clearTimeout(current.timer);
      current.source.classList.remove('sort-pressing');
      if (current.phase === 'pending') { drag = null; return; }
      current.phase = 'settling';
      cancelAnimationFrame(current.frame);
      ignoreClickUntil = performance.now() + 150;
      document.body.classList.remove('account-sorting');
      delete list.dataset.sorting;
      if (list.hasPointerCapture(current.pointerId)) list.releasePointerCapture(current.pointerId);
      const accepted = commit && isInside(current);
      animateLayout(current, () => {
        if (accepted) current.parent.insertBefore(current.source, current.slot);
        current.slot.remove();
        current.source.classList.remove('sort-source');
        current.source.classList.add('sort-settling');
      });
      const order = [...current.parent.children].filter(node => node.matches(current.selector));
      const changed = order.some((node, index) => node !== current.initialOrder[index]);
      if (accepted && changed) onCommit?.(current.kind, current.source);
      const destination = current.source.getBoundingClientRect();
      current.ghost.classList.add('is-dropping');
      const cleanup = () => {
        current.ghost.remove();
        current.source.classList.remove('sort-settling');
        if (accepted && changed) {
          current.source.classList.add('sort-dropped');
          setTimeout(() => current.source.classList.remove('sort-dropped'), 500);
        }
        if (drag === current) drag = null;
      };
      if (reducedMotion.matches) { cleanup(); return; }
      const landing = current.ghost.animate([
        {transform: current.ghost.style.transform, opacity: 1},
        {transform: `translate3d(${destination.left}px,${destination.top}px,0) scale(1)`, opacity: .6}
      ], {duration: 170, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'forwards'});
      landing.finished.then(cleanup, cleanup);
    };

    list.addEventListener('pointerdown', event => {
      if (drag || event.button !== 0 || event.isPrimary === false ||
        event.target.closest('input,select,textarea,a,.account-menu,button:not(.account-profile)')) return;
      const byCategory = list.dataset.groupMode === 'category';
      const account = byCategory ? null : event.target.closest('.account-item');
      const group = !account && event.target.closest('.platform-group > summary')?.parentElement;
      if (!account && !group) return;
      const source = account || group;
      const current = {source, parent: source.parentElement, kind: account ? 'account' : byCategory ? 'category' : 'platform',
        selector: account ? '.account-item' : '.platform-group', phase: 'pending',
        pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
        x: event.clientX, y: event.clientY, direction: 0, valid: true,
        handle: !!event.target.closest('[data-sort-handle]')};
      drag = current;
      source.classList.add('sort-pressing');
      current.timer = setTimeout(() => lift(current), 280);
    });
    document.addEventListener('pointermove', event => {
      const current = drag;
      if (!current || current.pointerId !== event.pointerId || current.phase === 'settling') return;
      const delta = event.clientY - current.y;
      current.x = event.clientX;
      current.y = event.clientY;
      if (Math.abs(delta) > 1) current.direction = Math.sign(delta);
      if (current.phase === 'pending') {
        if (Math.hypot(current.x - current.startX, current.y - current.startY) < 5) return;
        if (current.handle) lift(current);
        else { cancelledPointer = current.pointerId; ignoreClickUntil = performance.now() + 150; end(false); return; }
      }
      event.preventDefault();
      positionGhost(current);
      moveSlot(current);
    }, {passive: false});
    document.addEventListener('pointerup', event => {
      if (cancelledPointer === event.pointerId) { cancelledPointer = null; ignoreClickUntil = performance.now() + 150; }
      if (drag?.pointerId !== event.pointerId) return;
      if (drag.phase === 'sorting') event.preventDefault();
      end(true);
    });
    document.addEventListener('pointercancel', event => {
      if (cancelledPointer === event.pointerId) cancelledPointer = null;
      if (drag?.pointerId === event.pointerId) end(false);
    });
    list.addEventListener('lostpointercapture', () => { if (drag?.phase === 'sorting') end(false); });
    list.addEventListener('dragstart', event => event.preventDefault());
    list.addEventListener('contextmenu', event => { if (drag) event.preventDefault(); });
    document.addEventListener('click', event => {
      if (list.contains(event.target) && event.detail && performance.now() < ignoreClickUntil) {
        event.preventDefault(); event.stopImmediatePropagation();
      }
    }, true);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && drag) {
        event.preventDefault();
        if (drag.phase === 'pending') cancelledPointer = drag.pointerId;
        end(false);
      }
    });
    window.addEventListener('blur', () => end(false));
    return {get busy() { return drag !== null; }, cancel: () => end(false)};
  };
})();
