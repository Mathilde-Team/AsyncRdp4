/* SPDX-License-Identifier: GPL-3.0-or-later */
/* asyncrdp-image-provider.c — voir asyncrdp-image-provider.h pour le
 * contexte complet et la raison d'être de ce fichier. */
#include "asyncrdp-image-provider.h"

struct _AsyncrdpImageContentProvider
{
  GdkContentProvider parent_instance;

  GdkTexture *texture;   /* gardée pour do_get_value(), consommateurs GValue */
  GBytes *png_bytes;     /* encodée une seule fois, à la construction */
};

G_DEFINE_FINAL_TYPE(AsyncrdpImageContentProvider, asyncrdp_image_content_provider,
                     GDK_TYPE_CONTENT_PROVIDER)

static GdkContentFormats *
asyncrdp_image_content_provider_ref_formats(GdkContentProvider *provider)
{
  static const char *mime_types[] = { "image/png" };

  (void)provider;
  return gdk_content_formats_new(mime_types, G_N_ELEMENTS(mime_types));
}

static gboolean
asyncrdp_image_content_provider_get_value(GdkContentProvider *provider, GValue *value, GError **error)
{
  AsyncrdpImageContentProvider *self = ASYNCRDP_IMAGE_CONTENT_PROVIDER(provider);

  if (G_VALUE_HOLDS(value, GDK_TYPE_TEXTURE))
    {
      g_value_set_object(value, self->texture);
      return TRUE;
    }

  return GDK_CONTENT_PROVIDER_CLASS(asyncrdp_image_content_provider_parent_class)
      ->get_value(provider, value, error);
}

/* Callback interne à l'écriture, jamais exposé/marshalé côté Python : le
 * `task` ici est celui que CE fichier a construit lui-même juste en dessous,
 * pas celui que GDK a passé à write_mime_type_async — c'est justement ce
 * second GTask (celui de GDK) que PyGObject perdait quand cette classe
 * était écrite en Python. */
static void
on_write_bytes_ready(GObject *source, GAsyncResult *result, gpointer user_data)
{
  GTask *task = G_TASK(user_data);
  GError *error = NULL;
  gssize written = g_output_stream_write_bytes_finish(G_OUTPUT_STREAM(source), result, &error);

  if (written < 0)
    g_task_return_error(task, error);
  else
    g_task_return_boolean(task, TRUE);

  g_object_unref(task);
}

static void
asyncrdp_image_content_provider_write_mime_type_async(GdkContentProvider *provider,
                                                        const char *mime_type,
                                                        GOutputStream *stream,
                                                        int io_priority,
                                                        GCancellable *cancellable,
                                                        GAsyncReadyCallback callback,
                                                        gpointer user_data)
{
  AsyncrdpImageContentProvider *self = ASYNCRDP_IMAGE_CONTENT_PROVIDER(provider);
  GTask *task;

  (void)mime_type; /* un seul mime-type annoncé (image/png), pas besoin de brancher dessus */

  /* `callback`/`user_data` reçus ici viennent directement de
   * gdk_clipboard_write_async() côté C, sans jamais traverser un override
   * de vfunc Python — c'est exactement ce qui manquait à chaque tentative
   * précédente en PyGObject (voir docs/sessions/session-24.md). */
  task = g_task_new(provider, cancellable, callback, user_data);
  g_task_set_source_tag(task, asyncrdp_image_content_provider_write_mime_type_async);

  g_output_stream_write_bytes_async(stream, self->png_bytes, io_priority, cancellable,
                                     on_write_bytes_ready, task);
}

static gboolean
asyncrdp_image_content_provider_write_mime_type_finish(GdkContentProvider *provider,
                                                         GAsyncResult *result,
                                                         GError **error)
{
  g_return_val_if_fail(g_task_is_valid(result, provider), FALSE);
  return g_task_propagate_boolean(G_TASK(result), error);
}

static void
asyncrdp_image_content_provider_dispose(GObject *object)
{
  AsyncrdpImageContentProvider *self = ASYNCRDP_IMAGE_CONTENT_PROVIDER(object);

  g_clear_object(&self->texture);
  g_clear_pointer(&self->png_bytes, g_bytes_unref);

  G_OBJECT_CLASS(asyncrdp_image_content_provider_parent_class)->dispose(object);
}

static void
asyncrdp_image_content_provider_class_init(AsyncrdpImageContentProviderClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS(klass);
  GdkContentProviderClass *provider_class = GDK_CONTENT_PROVIDER_CLASS(klass);

  object_class->dispose = asyncrdp_image_content_provider_dispose;

  provider_class->ref_formats = asyncrdp_image_content_provider_ref_formats;
  provider_class->get_value = asyncrdp_image_content_provider_get_value;
  provider_class->write_mime_type_async = asyncrdp_image_content_provider_write_mime_type_async;
  provider_class->write_mime_type_finish = asyncrdp_image_content_provider_write_mime_type_finish;
}

static void
asyncrdp_image_content_provider_init(AsyncrdpImageContentProvider *self)
{
  (void)self;
}

GdkContentProvider *
asyncrdp_image_content_provider_new(GdkTexture *texture)
{
  AsyncrdpImageContentProvider *self;

  g_return_val_if_fail(GDK_IS_TEXTURE(texture), NULL);

  self = g_object_new(ASYNCRDP_TYPE_IMAGE_CONTENT_PROVIDER, NULL);
  self->texture = g_object_ref(texture);
  self->png_bytes = gdk_texture_save_to_png_bytes(texture);

  return GDK_CONTENT_PROVIDER(self);
}
