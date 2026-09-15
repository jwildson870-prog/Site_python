(function () {
  'use strict';

  var STORAGE_KEY = 'ppSidebarSections';

  function getState() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
    } catch (e) {
      return {};
    }
  }

  function saveState(state) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) { /* ignora ambientes sem localStorage */ }
  }

  function initSectionMemory() {
    var sections = document.querySelectorAll('.side-section[data-section]');
    if (!sections.length) return;
    var state = getState();

    sections.forEach(function (section) {
      var key = section.getAttribute('data-section');
      if (Object.prototype.hasOwnProperty.call(state, key)) {
        section.open = !!state[key];
      }
      section.addEventListener('toggle', function () {
        var current = getState();
        current[key] = section.open;
        saveState(current);
      });
    });
  }

  function normalize(text) {
    return (text || '')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }

  function initFilter() {
    var input = document.getElementById('sideNavFilter');
    var nav = document.getElementById('sideNav');
    if (!input || !nav) return;

    var links = Array.prototype.slice.call(nav.querySelectorAll('.side-link'));

    input.addEventListener('input', function () {
      var query = normalize(input.value.trim());
      var hasQuery = query.length > 0;

      links.forEach(function (link) {
        var label = link.querySelector('b');
        var text = normalize(label ? label.textContent : link.textContent);
        var matches = !hasQuery || text.indexOf(query) !== -1;
        link.classList.toggle('is-filtered-out', !matches);

        var section = link.closest('.side-section');
        if (section && matches && hasQuery) {
          section.open = true;
        }
      });

      nav.querySelectorAll('.side-section').forEach(function (section) {
        var visibleLinks = section.querySelectorAll('.side-link:not(.is-filtered-out)');
        section.classList.toggle('is-empty-filtered', hasQuery && visibleLinks.length === 0);
      });
    });

    input.addEventListener('keydown', function (evt) {
      if (evt.key === 'Escape') {
        input.value = '';
        input.dispatchEvent(new Event('input'));
        input.blur();
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initSectionMemory();
    initFilter();
  });
})();
