'use strict';

/**
 * What a control MEANS to the person about to use it, as a short string: its role, its label, the value
 * it holds, whether it is on or off, open or closed, usable or not, and the options it offers. Geometry
 * is left out on purpose (a control that moved is still the control), and so is anything under it that
 * does not name it. The same element yields the same string until one of those facts changes.
 *
 * Computed once when the page is observed and again just before an action is sent; if the two differ,
 * the ballot that chose the control described a page that is gone, and the action is refused unsent.
 *
 * This function runs INSIDE the page: it must stay self-contained (no closures, no imports), because
 * Playwright serialises it by source. Keep it in sync with nothing -- it is the only copy.
 * @param {Element} element the control
 * @returns {string} signature
 */
function semanticSignature(element) {
  const clean = (value) =>
    String(value || '')
      .replace(/\s+/g, ' ')
      .trim();
  const tag = element.tagName.toLowerCase();
  const role = element.getAttribute('role') || '';
  const label = clean(
    element.getAttribute('aria-label') ||
      (element.labels && element.labels.length ? [...element.labels].map((l) => l.innerText).join(' ') : '') ||
      element.getAttribute('title') ||
      element.getAttribute('placeholder') ||
      element.innerText ||
      element.textContent,
  ).slice(0, 80);
  const isField =
    (tag === 'input' && !['submit', 'button', 'checkbox', 'radio'].includes(element.type)) ||
    tag === 'textarea' ||
    tag === 'select';
  const value =
    tag === 'select'
      ? clean(element.selectedOptions && element.selectedOptions[0] ? element.selectedOptions[0].text : '')
      : isField
        ? clean(element.value).slice(0, 40)
        : '';
  const checked = element.type === 'checkbox' || element.type === 'radio' ? String(Boolean(element.checked)) : '';
  const expanded = element.hasAttribute('aria-expanded') ? element.getAttribute('aria-expanded') : '';
  const disabled = element.matches(':disabled') || element.getAttribute('aria-disabled') === 'true' ? 'disabled' : '';
  const options =
    tag === 'select'
      ? [...element.options]
          .map((o) => clean(o.text))
          .slice(0, 12)
          .join(',')
      : '';
  return [tag, role, label, value, checked, expanded, disabled, options].join('|');
}

module.exports = { semanticSignature };
