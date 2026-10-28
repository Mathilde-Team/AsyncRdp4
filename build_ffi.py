# SPDX-License-Identifier: GPL-3.0-or-later
"""
build_ffi.py — build cffi en mode API (out-of-line), pas ABI/dlopen.

Ce mode compile réellement un petit module d'extension C : le compilateur
résout les constantes d'enum FreeRDP_* depuis les vrais headers installés,
donc pas de valeurs numériques à deviner côté Python (contrairement à
l'approche ABI-mode/dlopen esquissée dans la première version du squelette).

Prérequis système (Debian/Ubuntu) :
    apt install libfreerdp-dev libfreerdp-client3-dev libwinpr-dev pkg-config

Utilisation :
    python build_ffi.py   # génère _asyncrdp_cffi.<arch>.so
"""

import os
import subprocess

from cffi import FFI

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHIM_PATH = os.path.join(_HERE, "src", "asyncrdp", "_shim.c")

ffibuilder = FFI()

# Signatures exposées par le shim — types primitifs uniquement,
# aucune constante d'enum FreeRDP ne traverse la frontière cffi.
ffibuilder.cdef("""
    typedef struct rdp_context rdpContext;
    typedef struct rdp_client_entry_points_v1 RDP_CLIENT_ENTRY_POINTS;
    typedef unsigned int UINT32;

    rdpContext* asyncrdp_context_new(const RDP_CLIENT_ENTRY_POINTS* entry);

    int asyncrdp_set_hostname(rdpContext* ctx, const char* host);
    int asyncrdp_set_port(rdpContext* ctx, UINT32 port);
    int asyncrdp_set_username(rdpContext* ctx, const char* username);
    int asyncrdp_set_password(rdpContext* ctx, const char* password);
    int asyncrdp_set_domain(rdpContext* ctx, const char* domain);
    int asyncrdp_set_resolution(rdpContext* ctx, UINT32 width, UINT32 height);
    int asyncrdp_set_color_depth(rdpContext* ctx, UINT32 bpp);
    int asyncrdp_set_security_nla(rdpContext* ctx, int enable_nla, int enable_tls);
    int asyncrdp_set_ignore_certificate(rdpContext* ctx, int ignore);

    int asyncrdp_set_redirect_clipboard(rdpContext* ctx, int enable);
    int asyncrdp_set_redirect_printers(rdpContext* ctx, int enable);
    int asyncrdp_set_redirect_smartcards(rdpContext* ctx, int enable);
    int asyncrdp_set_audio_playback(rdpContext* ctx, int enable);
    int asyncrdp_set_audio_capture(rdpContext* ctx, int enable);
    int asyncrdp_add_drive(rdpContext* ctx, const char* name, const char* path);
    int asyncrdp_set_redirect_drives(rdpContext* ctx, int enable);
    int asyncrdp_add_usb_device(rdpContext* ctx, const char* device_args);

    int asyncrdp_add_printer(rdpContext* ctx, const char* name,
                              const char* driver_name, int is_default);
    int asyncrdp_add_serial_port(rdpContext* ctx, const char* name, const char* path);
    int asyncrdp_add_parallel_port(rdpContext* ctx, const char* name, const char* path);

    int asyncrdp_set_use_multimon(rdpContext* ctx, int enable);
    int asyncrdp_set_monitors(rdpContext* ctx, const int* x, const int* y,
                              const int* width, const int* height,
                              const int* is_primary, unsigned int count);

    int asyncrdp_set_font_smoothing(rdpContext* ctx, int enable);
    int asyncrdp_set_desktop_composition(rdpContext* ctx, int enable);
    int asyncrdp_set_wallpaper(rdpContext* ctx, int enable);
    int asyncrdp_set_menu_animations(rdpContext* ctx, int enable);
    int asyncrdp_set_full_window_drag(rdpContext* ctx, int enable);
    int asyncrdp_set_themes(rdpContext* ctx, int enable);
    int asyncrdp_set_connection_type(rdpContext* ctx, unsigned int connection_type);

    int asyncrdp_set_gateway(rdpContext* ctx, const char* hostname, unsigned int port,
                              const char* username, const char* password, const char* domain,
                              unsigned int usage_method);
    int asyncrdp_set_remoteapp(rdpContext* ctx, const char* program, const char* name,
                                const char* cmdline);
    int asyncrdp_set_keyboard_layout(rdpContext* ctx, unsigned int klid);
    int asyncrdp_set_auto_reconnect(rdpContext* ctx, int enable);
    int asyncrdp_set_client_timezone(rdpContext* ctx);
    int asyncrdp_set_load_balance_info(rdpContext* ctx, const unsigned char* data, unsigned int length);
    int asyncrdp_set_graphics_pipeline(rdpContext* ctx, int enable_gfx, int enable_h264,
                                        int enable_h264_444, int enable_progressive);
    int asyncrdp_init_graphics_pipeline(rdpContext* ctx, void* gfx_ptr);
    void asyncrdp_uninit_graphics_pipeline(rdpContext* ctx, void* gfx_ptr);
    int asyncrdp_load_addins(rdpContext* ctx);

    int asyncrdp_get_handle_fds(rdpContext* ctx, int* fds_out, int max_count);
    int asyncrdp_check_event_handles(rdpContext* ctx);
    int asyncrdp_shall_disconnect(rdpContext* ctx);
    int asyncrdp_disconnect(rdpContext* ctx);

    int asyncrdp_connect(rdpContext* ctx);
    void asyncrdp_context_free(rdpContext* ctx);
    unsigned int asyncrdp_get_last_error(rdpContext* ctx);
    const char* asyncrdp_get_last_error_string(unsigned int code);

    int asyncrdp_gdi_init(rdpContext* ctx);
    void asyncrdp_set_end_paint_callback(rdpContext* ctx);
    int asyncrdp_get_framebuffer(rdpContext* ctx, unsigned char** data_out,
                                  int* width_out, int* height_out, int* stride_out);

    void asyncrdp_register_channels(rdpContext* ctx);
    int asyncrdp_clipboard_send_format_list(void* cliprdr_ptr,
                                             const unsigned int* format_ids,
                                             const char* const* format_names,
                                             unsigned int count);
    int asyncrdp_clipboard_send_data_response(void* cliprdr_ptr,
                                               const unsigned char* data,
                                               unsigned int data_len);
    int asyncrdp_clipboard_request_data(void* cliprdr_ptr, unsigned int format_id);

    int asyncrdp_clipboard_request_file_contents(void* cliprdr_ptr, unsigned int stream_id,
                                                  unsigned int list_index, unsigned int flags,
                                                  unsigned int position_low, unsigned int position_high,
                                                  unsigned int requested_size);
    int asyncrdp_clipboard_send_file_contents_response(void* cliprdr_ptr, unsigned int stream_id,
                                                        const unsigned char* data,
                                                        unsigned int data_len);
    int asyncrdp_send_resize(void* disp_ptr, unsigned int width, unsigned int height);

    int asyncrdp_send_mouse_move(rdpContext* ctx, unsigned short x, unsigned short y);
    int asyncrdp_send_mouse_button(rdpContext* ctx, unsigned short x, unsigned short y,
                                    unsigned int button_flag, int down);
    int asyncrdp_send_mouse_wheel(rdpContext* ctx, unsigned short x, unsigned short y, int delta);
    unsigned int asyncrdp_ptr_flag_button(const char* name);

    int asyncrdp_send_unicode_key(rdpContext* ctx, unsigned short code, int down);
    int asyncrdp_send_scancode_key(rdpContext* ctx, unsigned int rdp_scancode, int down);
    unsigned int asyncrdp_scancode_from_name(const char* name);

    // Callbacks implémentés côté Python (@ffi.def_extern()) — cffi génère
    // les vrais symboles C, référencés tels quels depuis asyncrdp_shim.c.
    extern "Python" int asyncrdp_on_end_paint(rdpContext* ctx);
    extern "Python" int asyncrdp_on_clipboard_format_list(
        rdpContext* ctx, void* cliprdr, unsigned int* format_ids,
        char** format_names, unsigned int count);
    extern "Python" int asyncrdp_on_clipboard_data_request(
        rdpContext* ctx, void* cliprdr, unsigned int format_id);
    extern "Python" int asyncrdp_on_clipboard_data_response(
        rdpContext* ctx, void* cliprdr, unsigned char* data, unsigned int data_len);
    extern "Python" int asyncrdp_on_clipboard_file_contents_request(
        rdpContext* ctx, void* cliprdr, unsigned int stream_id, unsigned int list_index,
        unsigned int flags, unsigned int position_low, unsigned int position_high,
        unsigned int requested_size);
    extern "Python" int asyncrdp_on_clipboard_file_contents_response(
        rdpContext* ctx, void* cliprdr, unsigned int stream_id,
        unsigned char* data, unsigned int data_len);
    extern "Python" int asyncrdp_on_disp_ready(rdpContext* ctx, void* disp);
    extern "Python" int asyncrdp_on_gfx_ready(rdpContext* ctx, void* gfx);
    extern "Python" int asyncrdp_on_cliprdr_ready(rdpContext* ctx, void* cliprdr);
""")

# pkg-config donne les bons include/lib dirs plutôt que de les coder en dur.
# freerdp-client3 est nécessaire en plus de freerdp3 pour les headers
# <freerdp/client/cliprdr.h> (API côté client, distincte de l'API core).
pkg_cflags = subprocess.run(
    ["pkg-config", "--cflags", "freerdp3", "freerdp-client3", "winpr3"], capture_output=True, text=True, check=True
).stdout.split()
pkg_libs = subprocess.run(
    ["pkg-config", "--libs", "freerdp3", "freerdp-client3", "winpr3"], capture_output=True, text=True, check=True
).stdout.split()

ffibuilder.set_source(
    "asyncrdp._asyncrdp_cffi",
    f'#include "{_SHIM_PATH}"',
    extra_compile_args=pkg_cflags,
    extra_link_args=pkg_libs,
)

if __name__ == "__main__":
    ffibuilder.compile(verbose=True)
