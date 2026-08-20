/* Public homepage panels.
 *
 * Rewritten for two reasons, both on the only page an unauthenticated visitor
 * sees (SITE_AUDIT_FINDINGS.md B20, B29):
 *
 *  1. Product names and SKUs entered by staff were interpolated into a template
 *     string and assigned with innerHTML. |json_script escapes for the script
 *     context, so JSON.parse returns the raw string and innerHTML then parses it
 *     as markup - a stored-XSS path on the public origin. Every node here is
 *     built with textContent, which cannot become markup.
 *  2. The currency was hardcoded: EUR rendered as a euro sign and the revenue
 *     figure was always prefixed with one and labelled "Revenue recorded in
 *     EUR", whatever BASE_CURRENCY was configured to. The server now supplies
 *     the code and the figure is labelled with it.
 */
(function () {
  "use strict";

  var source = document.getElementById("homepage-data");
  var dialog = document.getElementById("homepage-dialog");
  var content = document.getElementById("dialog-content");
  if (!source || !dialog || !content) return;

  var data;
  try {
    data = JSON.parse(source.textContent || "{}");
  } catch (e) {
    return;
  }

  var baseCurrency = data.base_currency || "EUR";

  function el(tag, text, className) {
    var node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }

  function money(value, code) {
    var amount = Number(value);
    if (!isFinite(amount)) return "—";
    /* Grouped per the visitor's locale; the code is always shown, so a figure
     * is never presented in an unstated currency. */
    return (
      (code || baseCurrency) +
      " " +
      amount.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    );
  }

  function tile(headline, caption) {
    var article = el("article", null, "live-product");
    article.appendChild(el("strong", headline));
    article.appendChild(el("small", caption));
    return article;
  }

  function shopPanel() {
    var frag = document.createDocumentFragment();
    frag.appendChild(el("h2", "Shop Our Collection"));
    frag.appendChild(
      el("p", "Live product and stock information from Stock & Material Master."),
    );
    var list = el("div", null, "live-products");
    var products = data.products || [];
    if (!products.length) {
      list.appendChild(el("p", "No products are published yet.", "empty"));
    }
    products.forEach(function (item) {
      var article = el("article", null, "live-product");
      var strong = el("strong", item.name);
      strong.appendChild(el("b", money(item.unit_cost, item.currency)));
      article.appendChild(strong);
      article.appendChild(
        el("small", item.sku + " · " + item.qty + " available"),
      );
      list.appendChild(article);
    });
    frag.appendChild(list);
    return frag;
  }

  function storyPanel() {
    var frag = document.createDocumentFragment();
    frag.appendChild(el("h2", "Irish Roots. Global Vision."));
    frag.appendChild(
      el(
        "p",
        "Emerald Rozalia is based in Limerick, Ireland, combining ethical " +
          "in-house manufacturing, craftsmanship and global ambition.",
      ),
    );
    var list = el("div", null, "live-products");
    list.appendChild(tile(data.countries + "+", "Countries represented"));
    list.appendChild(
      tile(data.customer_satisfaction + "%", "Customer satisfaction"),
    );
    list.appendChild(
      tile(
        money(data.annual_revenue, baseCurrency),
        "Revenue recorded in " + baseCurrency,
      ),
    );
    list.appendChild(tile(data.active_orders, "Active master orders"));
    frag.appendChild(list);
    return frag;
  }

  var panels = { shop: shopPanel, story: storyPanel };

  document.querySelectorAll("[data-panel]").forEach(function (trigger) {
    trigger.addEventListener("click", function () {
      var build = panels[trigger.dataset.panel];
      if (!build) return;
      content.replaceChildren(build());
      dialog.showModal();
    });
  });

  var close = dialog.querySelector(".dialog-close");
  if (close) close.addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) dialog.close();
  });
})();
