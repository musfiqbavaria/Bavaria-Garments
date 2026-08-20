/* Live-field grid for the seven public portal pages.
 *
 * This was an inline script that built each tile with innerHTML from a template
 * interpolation, so a StockItem name or SKU entered by staff was parsed as HTML
 * on a public page. |json_script escapes for the script context, which means
 * JSON.parse hands back the raw string and innerHTML then parses it as markup.
 * Nodes are built with textContent here, which cannot become markup, and the
 * file is external so the production CSP allows it.
 * See SITE_AUDIT_FINDINGS.md A5, B29.
 */
(function () {
  "use strict";
  var payload = document.getElementById("portal-live-data");
  var grid = document.getElementById("live-grid");
  if (!payload || !grid) return;

  var data;
  try {
    data = JSON.parse(payload.textContent);
  } catch (e) {
    return;
  }
  if (!data || typeof data !== "object") return;

  Object.keys(data).forEach(function (key) {
    var value = data[key];
    var article = document.createElement("article");
    var label = document.createElement("small");
    label.textContent = key.split("_").join(" ");
    var figure = document.createElement("b");
    figure.textContent = Array.isArray(value) ? String(value.length) : String(value);
    article.appendChild(label);
    article.appendChild(figure);
    grid.appendChild(article);
  });
})();
