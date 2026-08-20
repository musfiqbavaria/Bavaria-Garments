# locale/

Translation catalogues. `LOCALE_PATHS` points here and `LANGUAGES` in
`core/settings.py` lists what is offered: English, Bengali and Irish.

## State

The machinery is wired up - `LocaleMiddleware`, `LANGUAGES`, `LOCALE_PATHS`, a
language switcher in the navigation, and `{% trans %}` on the shared chrome
(`base.html`, the error pages, the navigation labels).

**The body copy of the 49 templates is still hardcoded English.** Marking it and
producing the Bengali and Irish translations is a content task, not a code one;
it needs a translator, and for payroll, QC and approval wording it needs sign-off
on the terms. Until then those strings fall through untranslated, which is the
correct behaviour - nothing breaks, it simply stays in English.

## Adding to a catalogue

```bash
docker compose exec -T web python manage.py makemessages -l bn
# edit locale/bn/LC_MESSAGES/django.po
docker compose exec -T web python manage.py compilemessages
```

`makemessages` needs GNU gettext installed in the image; it is not required at
runtime, only to regenerate a catalogue.

## Marking a string

In a template:

```
{% load i18n %}
<h1>{% trans "Cutting" %}</h1>
<p>{% blocktrans %}{{ count }} plans open today{% endblocktrans %}</p>
```

In Python, use `gettext_lazy` at module level and `gettext` inside a function -
never plain `gettext` for a module-level constant, or the string is frozen to
whichever language happened to be active at import.
