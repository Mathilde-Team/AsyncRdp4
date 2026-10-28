/* SPDX-License-Identifier: GPL-3.0-or-later */
/* asyncrdp-image-provider.h
 *
 * GdkContentProvider écrit en C pur, pas en sous-classe PyGObject.
 *
 * Raison d'être (voir CLAUDE.md / docs/features-backlog.md, section
 * presse-papier image, et docs/pygobject-async-vfunc-userdata-bug-report.md
 * pour le diagnostic complet) : sur ce GTK4 4.14.5 / PyGObject 3.48.2,
 * n'importe quelle sous-classe Python de Gdk.ContentProvider reçoit un
 * `user_data` déjà `None` dans son override de `do_write_mime_type_async`,
 * alors que le `GTask` C construit par `gdk_clipboard_write_async()` est
 * authentique et valide à cet instant précis (confirmé par lecture de
 * `gdk/gdkclipboard.c` et expérimentation directe le 2026-09-11). La perte
 * a lieu dans le marshaling PyGObject de CE vfunc précis au moment où GDK
 * l'appelle depuis le C vers un override Python — pas dans la façon dont
 * le code Python réutilise ensuite ce qu'il croit avoir reçu. Une sous-
 * classe GObject écrite entièrement en C ne traverse jamais cette frontière
 * pour cet appel : GDK invoque directement le pointeur de fonction C dans
 * la vtable, sans passer par le mécanisme de marshaling
 * vfunc-Python-override de PyGObject. Exposée à Python via GObject
 * Introspection (voir build_gir.py) uniquement pour la construction et
 * l'appel de méthodes normales — jamais pour une redéfinition de vfunc
 * côté Python.
 */
#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define ASYNCRDP_TYPE_IMAGE_CONTENT_PROVIDER (asyncrdp_image_content_provider_get_type())

G_DECLARE_FINAL_TYPE(AsyncrdpImageContentProvider, asyncrdp_image_content_provider,
                      ASYNCRDP, IMAGE_CONTENT_PROVIDER, GdkContentProvider)

/**
 * asyncrdp_image_content_provider_new:
 * @texture: (transfer none): texture à offrir sur le presse-papier, encodée
 *   en PNG une seule fois, de façon synchrone, à la construction (
 *   gdk_texture_save_to_png_bytes() ne peut pas échouer pour une texture
 *   valide, contrairement à un chargement de fichier).
 *
 * Returns: (transfer full): un nouveau fournisseur de contenu prêt à être
 *   posé sur un `Gdk.Clipboard` via `set_content()`.
 */
GdkContentProvider *asyncrdp_image_content_provider_new(GdkTexture *texture);

G_END_DECLS
