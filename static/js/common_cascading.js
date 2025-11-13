$(function () {
  // ----------------- config / helpers -----------------
  const STORE_KEY = "admin_electoral_slum_selection_v1";
  const $city = $('#id_City');
  const $admin = $('#id_AdministrativeWard');
  const $electoral = $('#id_ElectoralWard');
  const $slum = $('#id_slum_name');
  const $componentList = $('#componentList');
  const $refreshBtn = $('#refreshComponentList');
  const $componentTitle = $('#componentListTitle');

  function placeholderOption() {
    return $('<option>').val('').text('--Please select--');
  }

  function saveSelections() {
    try {
      const data = {
        city: $city.val() || '',
        administrative: $admin.val() || '',
        electoral: $electoral.val() || '',
        slum: $slum.val() || ''
      };
      sessionStorage.setItem(STORE_KEY, JSON.stringify(data));
    } catch (e) {
      // ignore
    }
  }

  function readSelections() {
    try {
      const raw = sessionStorage.getItem(STORE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function updateComponentListTitle() {
    const slumName = $slum.find('option:selected').text().trim();
    if (slumName) {
      $componentTitle.text(`Components List (Slum: ${slumName})`);
      $('#slum-selected-info').text(`Showing components for: ${slumName}`);
    } else {
      $componentTitle.text('Components List');
      $('#slum-selected-info').text('');
    }
  }

  // ----------------- AJAX loaders (return promises) -----------------
  function cityList() {
    $city.empty().append(placeholderOption());
    return $.ajax({
      url: city_url,
      type: 'GET',
      cache: false,
      dataType: 'json'
    }).then(function (json) {
      if (Array.isArray(json.nameArray) && Array.isArray(json.idArray)) {
        for (let i = 0; i < json.nameArray.length; i++) {
          $city.append($('<option>').val(json.idArray[i]).text(json.nameArray[i]));
        }
      }
      // restore saved city if present
      const saved = readSelections();
      if (saved && saved.city) {
        if ($city.find(`option[value="${saved.city}"]`).length) {
          $city.val(saved.city);
        } else {
          $city.val(saved.city); // keep value even if option missing
        }
      }
      return json;
    }).fail(function () {
      console.warn('Failed to load cities');
    });
  }

  function administrativewardList(cityId) {
    $admin.empty().append(placeholderOption());
    return $.ajax({
      url: administrative_url,
      type: 'POST',
      cache: false,
      data: { id: cityId }
    }).then(function (json) {
      if (Array.isArray(json.nameArray) && Array.isArray(json.idArray)) {
        for (let i = 0; i < json.nameArray.length; i++) {
          $admin.append($('<option>').val(json.idArray[i]).text(json.nameArray[i]));
        }
      }
      return json;
    }).fail(function () {
      console.warn('Failed to load administrative wards');
    });
  }

  function electoralWardList(adminId) {
    $electoral.empty().append(placeholderOption());
    return $.ajax({
      url: electoral_url,
      type: 'POST',
      cache: false,
      data: { id: adminId }
    }).then(function (json) {
      if (Array.isArray(json.nameArray) && Array.isArray(json.idArray)) {
        for (let i = 0; i < json.nameArray.length; i++) {
          $electoral.append($('<option>').val(json.idArray[i]).text(json.nameArray[i]));
        }
      }
      return json;
    }).fail(function () {
      console.warn('Failed to load electoral wards');
    });
  }

  function slumList(electoralId) {
    $slum.empty().append(placeholderOption());
    return $.ajax({
      url: slum_url,
      type: 'POST',
      cache: false,
      data: { id: electoralId }
    }).then(function (json) {
      if (Array.isArray(json.nameArray) && Array.isArray(json.idArray)) {
        for (let i = 0; i < json.nameArray.length; i++) {
          $slum.append($('<option>').val(json.idArray[i]).text(json.nameArray[i]));
        }
      }
      return json;
    }).fail(function () {
      console.warn('Failed to load slums');
    });
  }

  // load components for slum
  function loadComponentList(showAlerts=true) {
    const sid = $slum.val();
    updateComponentListTitle();
    if (!sid) {
      $componentList.find('.component-item').remove(); // clear items
      $componentList.prepend('<div class="list-group-item">Select a slum to view components.</div>');
      $refreshBtn.hide();
      return $.Deferred().resolve().promise();
    }

    $refreshBtn.show();
    $refreshBtn.prop('disabled', true).text('Refreshing...');

    return $.ajax({
      url: '/component/get_component_list/',
      type: 'GET',
      data: { object_id: sid },
      dataType: 'json',
      cache: false
    }).then(function (components) {
      // support array or object
      $componentList.empty();
      $('#slum-selected-info').text('');
      if (!components || (Array.isArray(components) && components.length === 0)) {
        $componentList.append('<div class="list-group-item">No components found.</div>');
        return components;
      }
      if (!Array.isArray(components) && typeof components === 'object') {
        // if server returns array under a key, try find it
        if (components.components) components = components.components;
        else components = [components];
      }
      components.forEach(function (comp) {
        const name = comp.name || comp;
        const $item = $(`
          <div class="list-group-item component-item" data-component-name="${escapeHtml(name)}" style="display:flex; justify-content:space-between; align-items:center;">
            <div>${escapeHtml(name)}</div>
            <button class="btn btn-danger btn-xs delete-component" type="button">Delete</button>
          </div>`);
        $componentList.append($item);
      });
      updateComponentListTitle();
      return components;
    }).fail(function () {
      if (showAlerts) alert('Failed to fetch components.');
    }).always(function () {
      $refreshBtn.prop('disabled', false).text('Refresh List');
    });
  }

  // safe html escape
  function escapeHtml(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;');
  }

  // filllist: server returns ancestry info for a slum id
  function filllist() {
    const id = $slum.val();
    if (!id) return $.Deferred().resolve().promise();

    return $.ajax({
      url: modelList_url,
      type: 'POST',
      data: { id: id },
      cache: false
    }).then(function (json) {
      // json expected: cid, aid, aname, eid, ename, sid, sname
      const cid = json.cid || '';
      const aid = json.aid || '';
      const eid = json.eid || '';
      const sid = json.sid || '';

      if (cid) $city.val(cid);
      return administrativewardList(cid).then(function () {
        if (aid) {
          if ($admin.find("option[value='" + aid + "']").length) {
            $admin.val(aid);
          } else {
            $admin.append($('<option>').val(aid).text(json.aname || 'Unknown'));
            $admin.val(aid);
          }
        }
        return electoralWardList(aid);
      }).then(function () {
        if (eid) {
          if ($electoral.find("option[value='" + eid + "']").length) {
            $electoral.val(eid);
          } else {
            $electoral.append($('<option>').val(eid).text(json.ename || 'Unknown'));
            $electoral.val(eid);
          }
        }
        return slumList(eid);
      }).then(function () {
        if (sid) {
          if ($slum.find("option[value='" + sid + "']").length) {
            $slum.val(sid);
          } else {
            $slum.append($('<option>').val(sid).text(json.sname || 'Unknown'));
            $slum.val(sid);
          }
        }
        // show UI and save selections
        $("#AdministrativeWards").show();
        $("#ElectoralWards").show();
        $("#Slums").show();
        saveSelections();

        // trigger change so component loader and title update
        $city.trigger('change');
        $admin.trigger('change');
        $electoral.trigger('change');
        $slum.trigger('change');

        return json;
      });
    }).fail(function () {
      console.warn('filllist failed');
    });
  }

  // ----------------- Delete handler for components -----------------
  $(document).on('click', '.delete-component', function (e) {
    e.preventDefault();
    const $btn = $(this);
    const compName = $btn.closest('.component-item').data('component-name');
    const sid = $slum.val();

    if (!sid) { alert('Please select a slum first!'); return; }
    if (!compName) { alert('Component name missing'); return; }
    if (!confirm(`Are you sure you want to delete "${compName}"?`)) return;

    $btn.prop('disabled', true);
    $.ajax({
      url: '/component/delete_component/',
      type: 'POST',
      data: {
        object_id: sid,
        comp_name: compName,
        csrfmiddlewaretoken: $('input[name="csrfmiddlewaretoken"]').val()
      }
    }).done(function (res) {
      alert(res && res.message ? res.message : `"${compName}" deleted`);
      $btn.closest('.component-item').remove();
    }).fail(function () {
      alert('Failed to delete component.');
      $btn.prop('disabled', false);
    });
  });

  // ----------------- Refresh button -----------------
  $refreshBtn.on('click', function () {
    loadComponentList();
  });

  // ----------------- Select change bindings -----------------
  $city.on('change', function () {
    const cid = $city.val();
    saveSelections();

    if (cid) {
      $("#AdministrativeWards").show();
      $("#ElectoralWards").hide();
      $("#Slums").hide();
      administrativewardList(cid).catch(()=>{});
    } else {
      $("#AdministrativeWards").hide();
      $("#ElectoralWards").hide();
      $("#Slums").hide();
    }
  });

  $admin.on('change', function () {
    const aid = $admin.val();
    saveSelections();

    if (aid) {
      $("#ElectoralWards").show();
      $("#Slums").hide();
      electoralWardList(aid).catch(()=>{});
    } else {
      $("#ElectoralWards").hide();
      $("#Slums").hide();
    }
  });

  $electoral.on('change', function () {
    const eid = $electoral.val();
    saveSelections();

    if (eid) {
      $("#Slums").show();
      slumList(eid).then(()=>{ saveSelections(); }).catch(()=>{});
    } else {
      $("#Slums").hide();
    }
  });

  $slum.on('change', function () {
    saveSelections();
    updateComponentListTitle();
    // load components automatically when slum selected
    loadComponentList();
  });

  // Show refresh button only when a slum is selected
  function toggleRefreshVisibility() {
    if ($slum.val()) $refreshBtn.show();
    else $refreshBtn.hide();
  }

  // ----------------- on page load: populate and restore -----------------
  (function init() {
    $("#AdministrativeWards").hide();
    $("#ElectoralWards").hide();
    $("#Slums").hide();
    $refreshBtn.hide();

    cityList().then(function () {
      const saved = readSelections();
      if (saved && saved.city) {
        // restore full chain from saved values
        $("#AdministrativeWards").show();
        administrativewardList(saved.city).then(function () {
          if (saved.administrative) {
            if ($admin.find("option[value='" + saved.administrative + "']").length) $admin.val(saved.administrative);
            $("#ElectoralWards").show();
          }
          return electoralWardList(saved.administrative);
        }).then(function () {
          if (saved.electoral) {
            if ($electoral.find("option[value='" + saved.electoral + "']").length) $electoral.val(saved.electoral);
            $("#Slums").show();
          }
          return slumList(saved.electoral);
        }).then(function () {
          if (saved.slum) {
            if ($slum.find("option[value='" + saved.slum + "']").length) $slum.val(saved.slum);
          }
          // If we have a slum (saved or server preloaded), call filllist() to ensure ancestry and UI state
          if ($slum.val()) {
            filllist().then(function () {
              toggleRefreshVisibility();
              loadComponentList(false);
            }).catch(()=>{ toggleRefreshVisibility(); loadComponentList(false); });
          } else {
            toggleRefreshVisibility();
            // no slum selected: clear components
            $componentList.empty().append('<div class="list-group-item">Select a slum to view components.</div>');
          }
        }).catch(()=>{ /* ignore */ });
      } else {
        // No saved selection: if server pre-rendered slum value, use filllist
        const preSlum = $slum.data('server-preload') || $slum.val();
        if (preSlum) {
          filllist().then(function () {
            toggleRefreshVisibility();
            loadComponentList(false);
          }).catch(()=>{ toggleRefreshVisibility(); loadComponentList(false); });
        } else {
          // nothing to restore
          $componentList.empty().append('<div class="list-group-item">Select a slum to view components.</div>');
        }
      }
    });
  })();

  // Save selection on form submit click
  $(document).on('click', 'form input[type="submit"], form button[type="submit"]', function () {
    saveSelections();
    // don't prevent default submit
  });

});
