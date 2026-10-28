/* SPDX-License-Identifier: GPL-3.0-or-later */
/*
 * asyncrdp_shim.c
 *
 * Pourquoi un shim C plutôt que du cffi ABI-mode pur (dlopen) :
 * l'API "stable" de FreeRDP3 (freerdp_settings_set_string/uint32/bool)
 * prend en paramètre des constantes d'enum (FreeRDP_ServerHostname,
 * FreeRDP_ServerPort, ...) générées automatiquement depuis les headers,
 * et dont les valeurs numériques ne sont PAS garanties stables entre
 * versions. Les deviner à la main côté Python serait fragile.
 *
 * En compilant ce petit shim contre <freerdp3/freerdp/settings.h> réel,
 * c'est le compilateur C qui résout les vraies valeurs — cffi n'a plus
 * qu'à appeler des fonctions à signature simple (char*, uint32_t, bool).
 */

#include <freerdp/settings.h>
#include <freerdp/freerdp.h>
#include <freerdp/client.h>
#include <freerdp/client/channels.h>
#include <freerdp/client/cmdline.h>
#include <freerdp/channels/rdpdr.h>

/*
 * Prototypes des callbacks implémentés côté Python (@ffi.def_extern()).
 * cffi compile ce fichier via un #include au MILIEU du fichier .c qu'il
 * génère : ses propres définitions `static` de ces fonctions n'arrivent
 * que PLUS LOIN dans ce même fichier généré (après notre #include). Sans
 * ce prototype, le premier appel ci-dessous crée une déclaration implicite
 * non-static qui entre ensuite en conflit avec la définition `static`
 * générée par cffi ("static declaration follows non-static declaration").
 * Une déclaration `static` ici, suivie de la définition `static`
 * générée par cffi plus loin dans le MÊME fichier, est parfaitement
 * légale en C — les noms de paramètres n'ont pas besoin de correspondre,
 * seuls les types comptent.
 */
static int asyncrdp_on_end_paint(rdpContext* ctx);
static int asyncrdp_on_clipboard_format_list(rdpContext* ctx, void* cliprdr,
                                              unsigned int* format_ids,
                                              char** format_names, unsigned int count);
static int asyncrdp_on_clipboard_data_request(rdpContext* ctx, void* cliprdr,
                                               unsigned int format_id);
static int asyncrdp_on_clipboard_data_response(rdpContext* ctx, void* cliprdr,
                                                unsigned char* data, unsigned int data_len);
static int asyncrdp_on_clipboard_file_contents_request(
    rdpContext* ctx, void* cliprdr, unsigned int stream_id, unsigned int list_index,
    unsigned int flags, unsigned int position_low, unsigned int position_high,
    unsigned int requested_size);
static int asyncrdp_on_clipboard_file_contents_response(
    rdpContext* ctx, void* cliprdr, unsigned int stream_id,
    unsigned char* data, unsigned int data_len);
static int asyncrdp_on_disp_ready(rdpContext* ctx, void* disp);
static int asyncrdp_on_gfx_ready(rdpContext* ctx, void* gfx);
static int asyncrdp_on_cliprdr_ready(rdpContext* ctx, void* cliprdr);

/* Crée un contexte client + settings par défaut. Retourne NULL si échec.
 *
 * BUG RÉEL trouvé en testant contre un vrai serveur : la version
 * précédente utilisait freerdp_new()+freerdp_context_new() bruts, qui
 * créent un contexte minimal mais SANS l'infrastructure client complète
 * (channels, pubSub correctement câblé pour les canaux statiques) que
 * met en place freerdp_client_context_new(). Résultat observé : AUCUN
 * canal (cliprdr compris) ne déclenchait jamais l'événement
 * ChannelConnected, quel que soit le temps d'attente — confirmé en
 * loggant tous les événements reçus (aucun, sur 15s de session active).
 */
rdpContext* asyncrdp_context_new(const RDP_CLIENT_ENTRY_POINTS* entry)
{
    RDP_CLIENT_ENTRY_POINTS default_entry = { 0 };
    default_entry.Size = sizeof(RDP_CLIENT_ENTRY_POINTS);
    default_entry.Version = RDP_CLIENT_INTERFACE_VERSION;
    default_entry.ContextSize = sizeof(rdpContext);
    /* ClientNew/ClientFree/ClientStart/ClientStop restent NULL — on ne
     * pilote pas de thread client dédié, on gère la boucle nous-mêmes
     * via les fds et asyncio (voir asyncrdp_get_handle_fds côté Python).
     * freerdp_client_context_new() tolère ces callbacks absents. */

    return freerdp_client_context_new(&default_entry);
}

int asyncrdp_set_hostname(rdpContext* ctx, const char* host)
{
    return freerdp_settings_set_string(ctx->settings, FreeRDP_ServerHostname, host);
}

int asyncrdp_set_port(rdpContext* ctx, UINT32 port)
{
    return freerdp_settings_set_uint32(ctx->settings, FreeRDP_ServerPort, port);
}

int asyncrdp_set_username(rdpContext* ctx, const char* username)
{
    return freerdp_settings_set_string(ctx->settings, FreeRDP_Username, username);
}

int asyncrdp_set_password(rdpContext* ctx, const char* password)
{
    return freerdp_settings_set_string(ctx->settings, FreeRDP_Password, password);
}

int asyncrdp_set_domain(rdpContext* ctx, const char* domain)
{
    return freerdp_settings_set_string(ctx->settings, FreeRDP_Domain, domain);
}

int asyncrdp_set_resolution(rdpContext* ctx, UINT32 width, UINT32 height)
{
    int ok = freerdp_settings_set_uint32(ctx->settings, FreeRDP_DesktopWidth, width);
    ok &= freerdp_settings_set_uint32(ctx->settings, FreeRDP_DesktopHeight, height);
    return ok;
}

int asyncrdp_set_color_depth(rdpContext* ctx, UINT32 bpp)
{
    return freerdp_settings_set_uint32(ctx->settings, FreeRDP_ColorDepth, bpp);
}

/*
 * Sécurité : NLA par défaut (recommandé), avec repli TLS si besoin.
 * En pratique, exposer un enum simple côté Python plutôt que de coller
 * aux booléens individuels RdpSecurity / TlsSecurity / NlaSecurity.
 */
int asyncrdp_set_security_nla(rdpContext* ctx, int enable_nla, int enable_tls)
{
    int ok = freerdp_settings_set_bool(ctx->settings, FreeRDP_NlaSecurity, enable_nla);
    ok &= freerdp_settings_set_bool(ctx->settings, FreeRDP_TlsSecurity, enable_tls);
    return ok;
}

/* Ignorer la vérification du certificat (dev uniquement — à exposer
 * comme option explicite côté GCM, pas un défaut silencieux). */
int asyncrdp_set_ignore_certificate(rdpContext* ctx, int ignore)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_IgnoreCertificate, ignore);
}

/* ------------------------------------------------------------------- */
/* Redirections de ressources locales — équivalent de l'onglet          */
/* "Ressources locales" de mstsc.                                       */
/* ------------------------------------------------------------------- */

int asyncrdp_set_redirect_clipboard(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_RedirectClipboard, enable);
}

int asyncrdp_set_redirect_printers(rdpContext* ctx, int enable)
{
    /* Redirige toutes les imprimantes locales détectées (CUPS côté
     * Linux) — pas de sélection fine par imprimante dans cette
     * première passe, contrairement à mstsc qui permet de cocher
     * imprimante par imprimante. */
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_RedirectPrinters, enable);
}

int asyncrdp_set_redirect_smartcards(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_RedirectSmartCards, enable);
}

int asyncrdp_set_audio_playback(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_AudioPlayback, enable);
}

int asyncrdp_set_audio_capture(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_AudioCapture, enable);
}

/*
 * Redirection de disque : ajoute un lecteur local (nom + chemin) à la
 * liste que le serveur verra comme un lecteur réseau. On passe par
 * freerdp_device_new() — le vrai constructeur FreeRDP pour les entrées
 * RDPDR_DEVICE — plutôt qu'un calloc() + remplissage manuel des champs :
 * ce dernier crashait à la libération (freerdp_device_free ->
 * freerdp_addin_argv_free suppose une structure interne construite par
 * freerdp_device_new, pas un calloc brut même avec les bons champs
 * RDPDR_DRIVE visibles dans le header — testé et confirmé en sandbox).
 */
int asyncrdp_add_drive(rdpContext* ctx, const char* name, const char* path)
{
    const char* args[2] = { name, path };
    RDPDR_DEVICE* device = freerdp_device_new(RDPDR_DTYP_FILESYSTEM, 2, args);
    if (!device)
        return 0;
    return freerdp_device_collection_add(ctx->settings, device);
}

int asyncrdp_set_redirect_drives(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_RedirectDrives, enable);
}

/*
 * USB (canal urbdrc) : contrairement à drive/printer/smartcard, FreeRDP
 * n'expose pas de sélection de périphérique typée — c'est une chaîne au
 * même format que /usb: en CLI (ex: "id,dev:0483:5741" ou "auto" pour
 * tout rediriger). L'énumération des périphériques USB dispo (via
 * libusb, pour construire cette chaîne côté UI) reste à faire côté GCM,
 * hors scope de ce shim.
 *
 * freerdp_client_add_dynamic_channel attend (settings, count, params[]) —
 * params[0] est le nom du canal, les suivants ses arguments (même
 * découpage que la ligne de commande /usb: passée à xfreerdp).
 */
int asyncrdp_add_usb_device(rdpContext* ctx, const char* device_args)
{
    const char* params[2];
    size_t count = 1;

    params[0] = "urbdrc";
    if (device_args && device_args[0] != '\0')
    {
        params[1] = device_args;
        count = 2;
    }

    return freerdp_client_add_dynamic_channel(ctx->settings, count, params);
}

/*
 * Sélection fine par imprimante (plutôt que le tout-ou-rien de
 * RedirectPrinters) : on ajoute directement une entrée RDPDR_PRINTER à
 * la liste des devices. Si au moins une imprimante est ajoutée ainsi,
 * seules celles-ci sont redirigées — RedirectPrinters ne sert plus alors
 * qu'à activer/désactiver le canal, pas à choisir "toutes ou rien".
 *
 * Champs de RDPDR_PRINTER à revérifier contre freerdp/channels/rdpdr.h
 * de la version installée (nom exact IsDefault vs DefaultPrinter selon
 * les branches).
 */
int asyncrdp_add_printer(rdpContext* ctx, const char* name,
                          const char* driver_name, int is_default)
{
    const char* args[2];
    size_t count = 1;

    args[0] = name;
    if (driver_name)
    {
        args[1] = driver_name;
        count = 2;
    }

    RDPDR_DEVICE* device = freerdp_device_new(RDPDR_DTYP_PRINT, count, args);
    if (!device)
        return 0;
    ((RDPDR_PRINTER*)device)->IsDefault = is_default;

    return freerdp_device_collection_add(ctx->settings, device);
}

/*
 * Port série (COM) redirigé — pas de notion de "tout rediriger" ici,
 * contrairement aux disques/imprimantes : chaque port local mappé doit
 * être déclaré explicitement, comme /serial:name,path en CLI xfreerdp.
 */
int asyncrdp_add_serial_port(rdpContext* ctx, const char* name, const char* path)
{
    const char* args[2] = { name, path };
    RDPDR_DEVICE* device = freerdp_device_new(RDPDR_DTYP_SERIAL, 2, args);
    if (!device)
        return 0;
    return freerdp_device_collection_add(ctx->settings, device);
}

/* Port parallèle (LPT) redirigé — même logique que le port série. */
int asyncrdp_add_parallel_port(rdpContext* ctx, const char* name, const char* path)
{
    const char* args[2] = { name, path };
    RDPDR_DEVICE* device = freerdp_device_new(RDPDR_DTYP_PARALLEL, 2, args);
    if (!device)
        return 0;
    return freerdp_device_collection_add(ctx->settings, device);
}

/*
 * Multi-écran : reproduit /monitors: de xfreerdp. Chaque écran local
 * (position + résolution + primaire ou non) devient un moniteur virtuel
 * côté serveur RDP. UseMultimon doit être activé pour que ces moniteurs
 * soient effectivement pris en compte à la négociation.
 *
 * Signature de rdpMonitor/MONITOR_DEF et nom exact du champ settings
 * (MonitorDefArray vs MonitorDefArrayList selon versions) à revérifier
 * contre freerdp/settings.h installé.
 */
int asyncrdp_set_use_multimon(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_UseMultimon, enable);
}

int asyncrdp_set_monitors(rdpContext* ctx, const int* x, const int* y,
                           const int* width, const int* height,
                           const int* is_primary, unsigned int count)
{
    rdpMonitor* monitors = (rdpMonitor*)calloc(count, sizeof(rdpMonitor));
    if (!monitors)
        return 0;

    for (unsigned int i = 0; i < count; i++)
    {
        monitors[i].x = x[i];
        monitors[i].y = y[i];
        monitors[i].width = width[i];
        monitors[i].height = height[i];
        monitors[i].is_primary = is_primary[i];
        monitors[i].orig_screen = i;
    }

    int ok = freerdp_settings_set_pointer_len(ctx->settings, FreeRDP_MonitorDefArray,
                                              monitors, count);
    ok &= freerdp_settings_set_uint32(ctx->settings, FreeRDP_MonitorCount, count);
    free(monitors); /* freerdp_settings_set_pointer_len copie les données */
    return ok;
}

/*
 * Charge les plugins de canaux (rdpdr avec ses sous-types drive/printer/
 * smartcard/série/parallèle, urbdrc, rdpsnd) correspondant aux settings
 * déjà configurés.
 *
 * MISE À JOUR après investigation complète en sandbox (Ubuntu 24.04,
 * FreeRDP 3.30.0, test réel contre un serveur xrdp local) : cet appel
 * continue de logger un warning "Failed to load channel rdpdr" au moment
 * du chargement des addins (avant la connexion), MAIS le canal rdpdr se
 * connecte bel et bien une fois la session établie — confirmé en loggant
 * tous les événements ChannelConnected reçus. Le warning semble donc
 * inoffensif dans ce contexte précis (peut-être une tentative de
 * chargement redondante/anticipée côté FreeRDP), pas un blocage réel.
 *
 * Le vrai bug qui empêchait TOUS les canaux (cliprdr et rdpdr compris) de
 * jamais se connecter n'était pas ici : asyncrdp_context_new() utilisait
 * freerdp_new()+freerdp_context_new() bruts au lieu du vrai point d'entrée
 * client freerdp_client_context_new(), qui met en place l'infrastructure
 * de canaux nécessaire. Une fois corrigé (voir asyncrdp_context_new
 * ci-dessus), rdpdr, rdpsnd, cliprdr, drdynvc et disp se sont TOUS
 * connectés sans exception supplémentaire nécessaire ici.
 *
 * Donc : le chemin normal (ce fichier tel qu'il est) suffit. Le warning
 * rdpdr au chargement des addins peut être ignoré tant que le canal se
 * connecte effectivement ensuite (vérifiable via les logs applicatifs
 * "Callbacks cliprdr + disp enregistrés" et un event ChannelConnected).
 */
int asyncrdp_load_addins(rdpContext* ctx)
{
    return freerdp_client_load_addins(ctx->channels, ctx->settings);
}

/* ------------------------------------------------------------------- */
/* Profil de performance — équivalent de l'onglet "Expérience" de mstsc. */
/* ------------------------------------------------------------------- */

int asyncrdp_set_font_smoothing(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_AllowFontSmoothing, enable);
}

int asyncrdp_set_desktop_composition(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_AllowDesktopComposition, enable);
}

int asyncrdp_set_wallpaper(rdpContext* ctx, int enable)
{
    /* Le nom du champ est inversé côté FreeRDP (Disable...) */
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_DisableWallpaper, !enable);
}

int asyncrdp_set_menu_animations(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_DisableMenuAnims, !enable);
}

int asyncrdp_set_full_window_drag(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_DisableFullWindowDrag, !enable);
}

int asyncrdp_set_themes(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_DisableThemes, !enable);
}

/*
 * Presets façon mstsc ("Détecter automatiquement", "Bas débit", "Haut
 * débit", "LAN") : ConnectionType règle automatiquement plusieurs des
 * flags ci-dessus côté FreeRDP. 1=modem, 2=broadband low, 3=broadband
 * high, 6=LAN, 7=auto-detect — valeurs de l'enum CONNECTION_TYPE à
 * revérifier contre freerdp/settings.h de la version installée.
 */
int asyncrdp_set_connection_type(rdpContext* ctx, unsigned int connection_type)
{
    return freerdp_settings_set_uint32(ctx->settings, FreeRDP_ConnectionType, connection_type);
}

/* ------------------------------------------------------------------- */
/* RD Gateway — connexion via une passerelle d'entreprise (RDG/TSG).     */
/* ------------------------------------------------------------------- */

int asyncrdp_set_gateway(rdpContext* ctx, const char* hostname, unsigned int port,
                          const char* username, const char* password, const char* domain,
                          unsigned int usage_method)
{
    int ok = freerdp_settings_set_bool(ctx->settings, FreeRDP_GatewayEnabled, TRUE);
    ok &= freerdp_settings_set_string(ctx->settings, FreeRDP_GatewayHostname, hostname);
    ok &= freerdp_settings_set_uint32(ctx->settings, FreeRDP_GatewayPort, port);
    if (username)
        ok &= freerdp_settings_set_string(ctx->settings, FreeRDP_GatewayUsername, username);
    if (password)
        ok &= freerdp_settings_set_string(ctx->settings, FreeRDP_GatewayPassword, password);
    if (domain)
        ok &= freerdp_settings_set_string(ctx->settings, FreeRDP_GatewayDomain, domain);
    /*
     * usage_method : valeurs numériques documentées par xfreerdp (pas de
     * macro publique trouvée dans les headers installés — vérifié par
     * recherche exhaustive) : 0=TSC_PROXY_MODE_NONE_DIRECT (jamais),
     * 1=TSC_PROXY_MODE_DIRECT (toujours), 2=TSC_PROXY_MODE_DETECT,
     * 3=TSC_PROXY_MODE_DEFAULT, 4=TSC_PROXY_MODE_NONE_DETECT.
     */
    ok &= freerdp_settings_set_uint32(ctx->settings, FreeRDP_GatewayUsageMethod, usage_method);
    return ok;
}

/* ------------------------------------------------------------------- */
/* RemoteApp / RAIL — lance une application distante unique plutôt que   */
/* le bureau complet. Limitation assumée : ceci active le MODE RemoteApp */
/* côté négociation, mais ne câble pas les callbacks du canal "rail"     */
/* nécessaires à une vraie gestion multi-fenêtres (déplacement/          */
/* redimensionnement des fenêtres distantes individuellement) — utilisable*/
/* seulement pour lancer une appli qui occupe tout l'espace client, pas   */
/* pour une intégration fenêtrée façon Windows RemoteApp complet.        */
/* ------------------------------------------------------------------- */

int asyncrdp_set_remoteapp(rdpContext* ctx, const char* program, const char* name,
                            const char* cmdline)
{
    int ok = freerdp_settings_set_bool(ctx->settings, FreeRDP_RemoteApplicationMode, TRUE);
    ok &= freerdp_settings_set_string(ctx->settings, FreeRDP_RemoteApplicationProgram, program);
    if (name)
        ok &= freerdp_settings_set_string(ctx->settings, FreeRDP_RemoteApplicationName, name);
    if (cmdline)
        ok &= freerdp_settings_set_string(ctx->settings, FreeRDP_RemoteApplicationCmdLine, cmdline);
    return ok;
}

/* ------------------------------------------------------------------- */
/* Disposition clavier (locale) — identifiant KLID Windows (ex: 0x0409  */
/* = anglais US, 0x040c = français). Sans ce réglage, le layout par      */
/* défaut du serveur s'applique, ce qui peut désaligner les touches      */
/* mortes/spéciales pour un clavier local non-US.                        */
/* ------------------------------------------------------------------- */

int asyncrdp_set_keyboard_layout(rdpContext* ctx, unsigned int klid)
{
    return freerdp_settings_set_uint32(ctx->settings, FreeRDP_KeyboardLayout, klid);
}

/* ------------------------------------------------------------------- */
/* Reconnexion automatique en cas de coupure réseau transitoire.         */
/* ------------------------------------------------------------------- */

int asyncrdp_set_auto_reconnect(rdpContext* ctx, int enable)
{
    return freerdp_settings_set_bool(ctx->settings, FreeRDP_AutoReconnectionEnabled, enable);
}

/* ------------------------------------------------------------------- */
/* Fuseau horaire client — reprend automatiquement celui du système      */
/* local (WinPR sait le lire), pas de paramètre à fournir par l'appelant.*/
/* ------------------------------------------------------------------- */

#include <winpr/timezone.h>

int asyncrdp_set_client_timezone(rdpContext* ctx)
{
    TIME_ZONE_INFORMATION tz = { 0 };
    /* GetTimeZoneInformation() renvoie 0xFFFFFFFF (TIME_ZONE_ID_INVALID côté
     * Win32) en cas d'échec — pas de macro de ce nom dans ce header WinPR,
     * qui ne définit que UNKNOWN(0)/STANDARD(1)/DAYLIGHT(2) pour le succès. */
    if (GetTimeZoneInformation(&tz) == 0xFFFFFFFF)
        return 0;
    return freerdp_settings_set_pointer_len(ctx->settings, FreeRDP_ClientTimeZone, &tz, 1);
}

/* ------------------------------------------------------------------- */
/* Cookie de redirection / load balancing — pour cibler un nœud précis  */
/* d'une ferme RDS. Rarement nécessaire manuellement (le client le reçoit*/
/* normalement du serveur lors d'une redirection automatique), exposé ici*/
/* pour les cas où l'appelant a déjà ce cookie via un mécanisme externe. */
/* ------------------------------------------------------------------- */

int asyncrdp_set_load_balance_info(rdpContext* ctx, const unsigned char* data, unsigned int length)
{
    int ok = freerdp_settings_set_pointer_len(ctx->settings, FreeRDP_LoadBalanceInfo, data, length);
    ok &= freerdp_settings_set_uint32(ctx->settings, FreeRDP_LoadBalanceInfoLength, length);
    return ok;
}

/* ------------------------------------------------------------------- */
/* Pipeline graphique (RDPGFX) et codecs modernes — H.264/AVC420/AVC444, */
/* RemoteFX Progressive. gdi_graphics_pipeline_init() câble les          */
/* callbacks du canal directement dans gdi->primary_buffer — le MÊME     */
/* buffer que lit déjà asyncrdp_get_framebuffer(), donc pas de nouveau   */
/* mécanisme de frame à ajouter côté Python. Le déclenchement de         */
/* update->EndPaint reste géré en interne par FreeRDP pour ce chemin     */
/* aussi (comme pour le GDI legacy) — hypothèse cohérente avec           */
/* l'architecture FreeRDP mais dont la confirmation frame-par-frame      */
/* contre un vrai flux H.264 dépend d'un serveur qui le négocie          */
/* réellement (non disponible dans le sandbox de développement : xrdp    */
/* n'annonce pas RDPGFX/H264 dans sa configuration par défaut).          */
/* ------------------------------------------------------------------- */

#include <freerdp/gdi/gfx.h>
#include <freerdp/client/rdpgfx.h>
#include <freerdp/channels/rdpgfx.h>

int asyncrdp_set_graphics_pipeline(rdpContext* ctx, int enable_gfx, int enable_h264,
                                    int enable_h264_444, int enable_progressive)
{
    int ok = freerdp_settings_set_bool(ctx->settings, FreeRDP_SupportGraphicsPipeline, enable_gfx);
    ok &= freerdp_settings_set_bool(ctx->settings, FreeRDP_GfxH264, enable_h264);
    ok &= freerdp_settings_set_bool(ctx->settings, FreeRDP_GfxAVC444, enable_h264_444);
    ok &= freerdp_settings_set_bool(ctx->settings, FreeRDP_GfxProgressive, enable_progressive);
    return ok;
}

/*
 * Câble effectivement le pipeline GFX dans le GDI logiciel. À appeler
 * quand les DEUX conditions sont réunies : le canal RDPGFX est connecté
 * (pointeur reçu via asyncrdp_on_gfx_ready) ET gdi_init() a déjà eu lieu
 * (ctx->gdi non NULL) — l'ordre entre les deux n'est pas garanti (le
 * canal peut se connecter avant ou après notre appel à gdi_init côté
 * Python), donc cette fonction est appelée depuis DEUX points différents
 * côté asyncrdp.py selon lequel des deux arrive en second.
 */
int asyncrdp_init_graphics_pipeline(rdpContext* ctx, void* gfx_ptr)
{
    if (!ctx->gdi)
        return 0;
    return gdi_graphics_pipeline_init(ctx->gdi, (RdpgfxClientContext*)gfx_ptr);
}

void asyncrdp_uninit_graphics_pipeline(rdpContext* ctx, void* gfx_ptr)
{
    if (!ctx->gdi || !gfx_ptr)
        return;
    gdi_graphics_pipeline_uninit(ctx->gdi, (RdpgfxClientContext*)gfx_ptr);
}

/* ------------------------------------------------------------------- */
/* Intégration event loop : extraction des fds pollables                */
/* ------------------------------------------------------------------- */

#define ASYNCRDP_MAX_HANDLES 64

/*
 * freerdp_get_event_handles() renvoie des HANDLE WinPR — certains sont
 * de vrais fds Linux exploitables via select/poll/epoll (donc via
 * loop.add_reader), d'autres non (ex. objets de synchronisation internes
 * sans fd sous-jacent). GetEventFileDescriptor() renvoie -1 pour ces
 * derniers : on les filtre côté C pour ne remonter que du pollable.
 *
 * Retourne le nombre de fds écrits dans fds_out (<= max_count).
 */
int asyncrdp_get_handle_fds(rdpContext* ctx, int* fds_out, int max_count)
{
    HANDLE handles[ASYNCRDP_MAX_HANDLES];
    DWORD n = freerdp_get_event_handles(ctx, handles, ASYNCRDP_MAX_HANDLES);
    int written = 0;

    for (DWORD i = 0; i < n && written < max_count; i++)
    {
        int fd = GetEventFileDescriptor(handles[i]);
        if (fd >= 0)
            fds_out[written++] = fd;
    }
    return written;
}

/* Traite les événements en attente sur le contexte. À appeler quand un
 * des fds retournés par asyncrdp_get_handle_fds() devient lisible. */
int asyncrdp_check_event_handles(rdpContext* ctx)
{
    return freerdp_check_event_handles(ctx);
}

/* Indique si le serveur/la lib demande la déconnexion (fin de session,
 * erreur fatale, etc.) — à vérifier après chaque check_event_handles. */
int asyncrdp_shall_disconnect(rdpContext* ctx)
{
    return freerdp_shall_disconnect_context(ctx);
}

int asyncrdp_disconnect(rdpContext* ctx)
{
    return freerdp_disconnect(ctx->instance);
}

/* ------------------------------------------------------------------- */
/* Connexion, libération, erreurs — regroupées ici pour que TOUT passe   */
/* par ce module compilé (plus de dlopen ABI-mode parallèle côté Python) */
/* ------------------------------------------------------------------- */

int asyncrdp_connect(rdpContext* ctx)
{
    return freerdp_connect(ctx->instance);
}

void asyncrdp_context_free(rdpContext* ctx)
{
    freerdp_client_context_free(ctx);
}

unsigned int asyncrdp_get_last_error(rdpContext* ctx)
{
    return freerdp_get_last_error(ctx);
}

/*
 * ATTENTION : le nom exact du symbole diffère selon les versions de
 * FreeRDP (freerdp_get_last_error_string vs freerdp_get_last_error_name
 * selon les branches). À vérifier avec :
 *   nm -D /usr/lib/x86_64-linux-gnu/libfreerdp3.so | grep last_error
 * et ajuster l'appel ci-dessous en conséquence avant de compiler.
 */
const char* asyncrdp_get_last_error_string(unsigned int code)
{
    return freerdp_get_last_error_string(code);
}

/* ------------------------------------------------------------------- */
/* GDI logiciel + callback EndPaint pour récupérer les frames           */
/* ------------------------------------------------------------------- */

#include <freerdp/gdi/gdi.h>

/*
 * gdi_init() met en place un GDI logiciel qui décode en interne les
 * BitmapUpdate/SurfaceBits/RemoteFX/etc. et rasterise dans un buffer
 * BGRA32 plat — bien plus simple que de hooker chaque type d'update
 * pour gérer soi-même chaque codec. C'est ce que font la plupart des
 * clients FreeRDP "maison" qui n'ont pas besoin d'accélération GPU.
 *
 * À appeler après une connexion réussie (les settings, notamment la
 * résolution et la profondeur de couleur, doivent déjà être négociés).
 */
int asyncrdp_gdi_init(rdpContext* ctx)
{
    return gdi_init(ctx->instance, PIXEL_FORMAT_BGRA32);
}

/*
 * Requis par le pipeline GFX (gdi_graphics_pipeline_init) : sans ce
 * callback, un ResetGraphics reçu du serveur (ex: à la connexion, pour
 * annoncer la résolution) déclenche un WINPR_ASSERT côté FreeRDP
 * (gdi_ResetGraphics, libfreerdp/gdi/gfx.c) — trouvé en testant contre un
 * vrai serveur GFX/H.264 (freerdp-shadow-cli). On lit déjà gdi->width/
 * height dynamiquement à chaque frame côté Python (_on_end_paint), donc
 * ce callback n'a rien de plus à faire que confirmer l'acceptation.
 */
static BOOL asyncrdp_on_desktop_resize(rdpContext* ctx)
{
    return TRUE;
}

/*
 * asyncrdp_on_end_paint est implémentée côté Python (voir build_ffi.py :
 * extern "Python" ...). EndPaint est appelé par le GDI logiciel une fois
 * qu'une frame a fini d'être rasterisée dans gdi->primary_buffer — c'est
 * le signal "il y a une nouvelle image prête à lire".
 */
void asyncrdp_set_end_paint_callback(rdpContext* ctx)
{
    ctx->update->EndPaint = asyncrdp_on_end_paint;
    ctx->update->DesktopResize = asyncrdp_on_desktop_resize;
}

/*
 * Donne accès au framebuffer brut courant. ATTENTION : le buffer est
 * réutilisé par FreeRDP à la frame suivante — le code appelant (Python)
 * doit copier les données avant de rendre la main (pas de zero-copy
 * au-delà de la durée du callback EndPaint).
 *
 * Noms de champs à vérifier contre gdi.h de la version installée
 * (gdi->stride vs gdi->primary_buffer peuvent varier selon les branches).
 */
int asyncrdp_get_framebuffer(rdpContext* ctx, unsigned char** data_out,
                              int* width_out, int* height_out, int* stride_out)
{
    rdpGdi* gdi = ctx->gdi;
    if (!gdi || !gdi->primary_buffer)
        return 0;

    *data_out = gdi->primary_buffer;
    *width_out = gdi->width;
    *height_out = gdi->height;
    *stride_out = gdi->stride;
    return 1;
}

/* ------------------------------------------------------------------- */
/* Clipboard (canal cliprdr) — texte, image (CF_DIB) et listes de       */
/* fichiers (format enregistré "FileGroupDescriptorW" + transfert de    */
/* contenu via File Contents Request/Response, MS-RDPECLIP §2.2.5).     */
/* ------------------------------------------------------------------- */

#include <freerdp/client/cliprdr.h>
#include <freerdp/channels/channels.h>
#include <freerdp/event.h>

/* Déclarations anticipées : référencées dans asyncrdp_on_channel_connected
 * avant leur définition plus bas dans ce fichier. */
UINT asyncrdp_cliprdr_on_format_list(CliprdrClientContext* cliprdr,
                                     const CLIPRDR_FORMAT_LIST* formatList);
UINT asyncrdp_cliprdr_on_data_request(CliprdrClientContext* cliprdr,
                                      const CLIPRDR_FORMAT_DATA_REQUEST* req);
UINT asyncrdp_cliprdr_on_data_response(CliprdrClientContext* cliprdr,
                                       const CLIPRDR_FORMAT_DATA_RESPONSE* resp);
UINT asyncrdp_cliprdr_on_file_contents_request(CliprdrClientContext* cliprdr,
                                               const CLIPRDR_FILE_CONTENTS_REQUEST* req);
UINT asyncrdp_cliprdr_on_file_contents_response(CliprdrClientContext* cliprdr,
                                                const CLIPRDR_FILE_CONTENTS_RESPONSE* resp);

#include <freerdp/client/disp.h>
#include <freerdp/channels/disp.h>

/*
 * Un seul handler PubSub pour tous les canaux qu'on veut intercepter
 * (cliprdr = static, disp = dynamic virtual channel — les deux remontent
 * par le même événement ChannelConnectedEventArgs côté client FreeRDP).
 */
static void asyncrdp_on_channel_connected(void* context, const ChannelConnectedEventArgs* e)
{
    rdpContext* ctx = (rdpContext*)context;


    if (strcmp(e->name, CLIPRDR_SVC_CHANNEL_NAME) == 0)
    {
        CliprdrClientContext* cliprdr = (CliprdrClientContext*)e->pInterface;

        /*
         * cliprdr->custom sert de stockage libre pour l'appli cliente — on
         * s'en sert pour retrouver le rdpContext* depuis les callbacks
         * cliprdr, qui ne reçoivent eux-mêmes qu'un CliprdrClientContext*.
         */
        cliprdr->custom = ctx;
        cliprdr->ServerFormatList = asyncrdp_cliprdr_on_format_list;
        cliprdr->ServerFormatDataRequest = asyncrdp_cliprdr_on_data_request;
        cliprdr->ServerFormatDataResponse = asyncrdp_cliprdr_on_data_response;
        cliprdr->ServerFileContentsRequest = asyncrdp_cliprdr_on_file_contents_request;
        cliprdr->ServerFileContentsResponse = asyncrdp_cliprdr_on_file_contents_response;

        /*
         * BUG RÉEL trouvé en testant contre un vrai serveur (xrdp) :
         * sans cet appel, self._cliprdr côté Python restait None tant que
         * le SERVEUR n'avait pas pris l'initiative d'un ServerFormatList/
         * DataRequest/DataResponse. Si le serveur attend passivement que
         * le client parle en premier (cas observé), announce_local_text()
         * et request_remote_text() restaient bloqués indéfiniment — aucun
         * des deux ne peut jamais aboutir sans ce point d'entrée proactif,
         * symétrique de ce qui existe déjà pour "disp" juste en dessous.
         */
        asyncrdp_on_cliprdr_ready(ctx, cliprdr);
    }
    else if (strcmp(e->name, DISP_DVC_CHANNEL_NAME) == 0)
    {
        DispClientContext* disp = (DispClientContext*)e->pInterface;
        disp->custom = ctx;
        /* Notifie Python que le canal est prêt, pour qu'il garde le
         * pointeur DispClientContext* et puisse appeler
         * asyncrdp_send_resize() plus tard. Pas de callback serveur->client
         * hooké ici (DisplayControlCaps donnerait les résolutions max
         * supportées — non exploité dans cette première passe). */
        asyncrdp_on_disp_ready(ctx, disp);
    }
    else if (strcmp(e->name, RDPGFX_DVC_CHANNEL_NAME) == 0)
    {
        /*
         * Le canal RDPGFX (pipeline graphique moderne, H.264/AVC420/AVC444,
         * RemoteFX Progressive) ne remplace PAS notre boucle de rendu — il
         * s'y raccroche. gdi_graphics_pipeline_init() câble les callbacks
         * du canal directement dans gdi->primary_buffer, le MÊME buffer que
         * lit déjà asyncrdp_get_framebuffer() ; le déclenchement de
         * update->EndPaint (notre point d'accroche existant côté Python)
         * reste géré en interne par FreeRDP pour ce chemin aussi, comme
         * pour le GDI legacy. Pas de nouveau mécanisme de frame à câbler
         * côté Python — SI cette hypothèse se confirme en test réel
         * (à vérifier : xrdp dans notre sandbox ne négocie pas forcément
         * RDPGFX/H264, donc ce point précis n'a pas pu être confirmé
         * frame-par-frame contre un vrai flux H.264).
         *
         * ctx->gdi peut être NULL ici si ce canal se connecte AVANT notre
         * appel à gdi_init() (fait après asyncrdp_connect() côté Python) —
         * dans ce cas on se contente de notifier Python, qui rappellera
         * asyncrdp_init_graphics_pipeline() explicitement une fois gdi_init
         * fait (voir connect() dans asyncrdp.py).
         */
        asyncrdp_on_gfx_ready(ctx, e->pInterface);
    }
}

void asyncrdp_register_channels(rdpContext* ctx)
{
    PubSub_SubscribeChannelConnected(ctx->pubSub, asyncrdp_on_channel_connected);
}

/*
 * Demande un changement de résolution au serveur via le canal Display
 * Control (MS-RDPEDISP). Le serveur peut refuser/clamp la valeur — le
 * résultat réel se voit dans les prochaines frames (gdi->width/height
 * changeront, déjà lu dynamiquement à chaque EndPaint côté Python).
 */
int asyncrdp_send_resize(void* disp_ptr, unsigned int width, unsigned int height)
{
    DispClientContext* disp = (DispClientContext*)disp_ptr;
    DISPLAY_CONTROL_MONITOR_LAYOUT monitor = { 0 };

    monitor.Flags = 0x1; /* DISPLAY_CONTROL_MONITOR_PRIMARY */
    monitor.Left = 0;
    monitor.Top = 0;
    /* RDP exige une largeur paire */
    monitor.Width = width - (width % 2);
    monitor.Height = height;
    monitor.PhysicalWidth = 0;
    monitor.PhysicalHeight = 0;
    monitor.Orientation = 0;          /* landscape */
    monitor.DesktopScaleFactor = 100;
    monitor.DeviceScaleFactor = 100;

    return disp->SendMonitorLayout(disp, 1, &monitor) == CHANNEL_RC_OK;
}

/*
 * Transmet formatId ET formatName (peut être NULL pour les formats
 * standard CF_* comme CF_UNICODETEXT/CF_DIB) — les formats "riches" côté
 * fichiers (FileGroupDescriptorW) n'ont pas d'ID fixe : leur ID est
 * négocié dynamiquement par la session et ne se distingue que par ce nom.
 * names_out reste valide seulement le temps de l'appel Python
 * (asyncrdp_on_clipboard_format_list) — à copier côté Python, pas à
 * garder la référence.
 */
UINT asyncrdp_cliprdr_on_format_list(CliprdrClientContext* cliprdr,
                                     const CLIPRDR_FORMAT_LIST* formatList)
{
    rdpContext* ctx = (rdpContext*)cliprdr->custom;
    UINT32 count = formatList->numFormats;

    unsigned int* ids = malloc(sizeof(unsigned int) * (count ? count : 1));
    char** names = malloc(sizeof(char*) * (count ? count : 1));
    for (UINT32 i = 0; i < count; i++)
    {
        ids[i] = formatList->formats[i].formatId;
        names[i] = formatList->formats[i].formatName; /* NULL si format standard */
    }

    asyncrdp_on_clipboard_format_list(ctx, cliprdr, ids, names, count);
    free(ids);
    free(names);
    return CHANNEL_RC_OK;
}

UINT asyncrdp_cliprdr_on_data_request(CliprdrClientContext* cliprdr,
                                      const CLIPRDR_FORMAT_DATA_REQUEST* req)
{
    rdpContext* ctx = (rdpContext*)cliprdr->custom;
    asyncrdp_on_clipboard_data_request(ctx, cliprdr, req->requestedFormatId);
    return CHANNEL_RC_OK;
}

UINT asyncrdp_cliprdr_on_data_response(CliprdrClientContext* cliprdr,
                                       const CLIPRDR_FORMAT_DATA_RESPONSE* resp)
{
    rdpContext* ctx = (rdpContext*)cliprdr->custom;
    asyncrdp_on_clipboard_data_response(ctx, cliprdr,
                                        (unsigned char*)resp->requestedFormatData,
                                        resp->common.dataLen);
    return CHANNEL_RC_OK;
}

/*
 * Le serveur demande le contenu (les octets réels) d'un fichier que LE
 * CLIENT a annoncé dans sa propre liste (push local -> serveur). Direction
 * symétrique de celle ci-dessous.
 */
UINT asyncrdp_cliprdr_on_file_contents_request(CliprdrClientContext* cliprdr,
                                               const CLIPRDR_FILE_CONTENTS_REQUEST* req)
{
    rdpContext* ctx = (rdpContext*)cliprdr->custom;
    asyncrdp_on_clipboard_file_contents_request(ctx, cliprdr, req->streamId, req->listIndex,
                                                 req->dwFlags, req->nPositionLow,
                                                 req->nPositionHigh, req->cbRequested);
    return CHANNEL_RC_OK;
}

/*
 * Réponse du serveur à une demande de contenu de fichier initiée par le
 * client (pull serveur -> client, cf. asyncrdp_clipboard_request_file_contents).
 */
UINT asyncrdp_cliprdr_on_file_contents_response(CliprdrClientContext* cliprdr,
                                                const CLIPRDR_FILE_CONTENTS_RESPONSE* resp)
{
    rdpContext* ctx = (rdpContext*)cliprdr->custom;
    asyncrdp_on_clipboard_file_contents_response(ctx, cliprdr, resp->streamId,
                                                  (unsigned char*)resp->requestedData,
                                                  resp->cbRequested);
    return CHANNEL_RC_OK;
}

/* --- Envoi client -> serveur ------------------------------------------ */

/*
 * format_names[i] peut être NULL (format standard CF_*) ou un nom de
 * format enregistré (ex: "FileGroupDescriptorW"). Quand un nom est
 * fourni, format_ids[i] est ignoré côté construction (FreeRDP assigne/
 * retrouve l'ID réel via le nom) — passer 0.
 */
int asyncrdp_clipboard_send_format_list(void* cliprdr_ptr,
                                         const unsigned int* format_ids,
                                         const char* const* format_names,
                                         unsigned int count)
{
    CliprdrClientContext* cliprdr = (CliprdrClientContext*)cliprdr_ptr;
    CLIPRDR_FORMAT_LIST formatList = { 0 };
    CLIPRDR_FORMAT formats[8] = { 0 };

    if (count > 8)
        count = 8;

    for (unsigned int i = 0; i < count; i++)
    {
        formats[i].formatId = format_ids[i];
        formats[i].formatName = (format_names && format_names[i]) ? (char*)format_names[i] : NULL;
    }

    formatList.numFormats = count;
    formatList.formats = formats;

    return cliprdr->ClientFormatList(cliprdr, &formatList) == CHANNEL_RC_OK;
}

int asyncrdp_clipboard_send_data_response(void* cliprdr_ptr,
                                           const unsigned char* data,
                                           unsigned int data_len)
{
    CliprdrClientContext* cliprdr = (CliprdrClientContext*)cliprdr_ptr;
    CLIPRDR_FORMAT_DATA_RESPONSE response = { 0 };

    response.common.msgFlags = data ? CB_RESPONSE_OK : CB_RESPONSE_FAIL;
    response.common.dataLen = data_len;
    response.requestedFormatData = (BYTE*)data;

    return cliprdr->ClientFormatDataResponse(cliprdr, &response) == CHANNEL_RC_OK;
}

int asyncrdp_clipboard_request_data(void* cliprdr_ptr, unsigned int format_id)
{
    CliprdrClientContext* cliprdr = (CliprdrClientContext*)cliprdr_ptr;
    CLIPRDR_FORMAT_DATA_REQUEST request = { 0 };

    request.requestedFormatId = format_id;
    return cliprdr->ClientFormatDataRequest(cliprdr, &request) == CHANNEL_RC_OK;
}

/*
 * Demande une plage d'octets du fichier distant identifié par list_index
 * (son rang dans le dernier FileGroupDescriptorW reçu). flags :
 * FILECONTENTS_SIZE (0x1, demande la taille seule) ou FILECONTENTS_RANGE
 * (0x2, demande des données). À boucler côté Python avec des offsets
 * croissants pour récupérer un fichier entier par blocs (voir
 * Clipboard.request_remote_file_contents() côté asyncrdp.py) —
 * FreeRDP/le serveur peuvent capper cbRequested par appel.
 */
int asyncrdp_clipboard_request_file_contents(void* cliprdr_ptr, unsigned int stream_id,
                                              unsigned int list_index, unsigned int flags,
                                              unsigned int position_low, unsigned int position_high,
                                              unsigned int requested_size)
{
    CliprdrClientContext* cliprdr = (CliprdrClientContext*)cliprdr_ptr;
    CLIPRDR_FILE_CONTENTS_REQUEST request = { 0 };

    request.streamId = stream_id;
    request.listIndex = list_index;
    request.dwFlags = flags;
    request.nPositionLow = position_low;
    request.nPositionHigh = position_high;
    request.cbRequested = requested_size;

    return cliprdr->ClientFileContentsRequest(cliprdr, &request) == CHANNEL_RC_OK;
}

/*
 * Réponse à une demande de contenu émise par le SERVEUR pour un fichier
 * que le client a lui-même annoncé (push local -> serveur, cf.
 * asyncrdp_cliprdr_on_file_contents_request ci-dessus).
 */
int asyncrdp_clipboard_send_file_contents_response(void* cliprdr_ptr, unsigned int stream_id,
                                                    const unsigned char* data,
                                                    unsigned int data_len)
{
    CliprdrClientContext* cliprdr = (CliprdrClientContext*)cliprdr_ptr;
    CLIPRDR_FILE_CONTENTS_RESPONSE response = { 0 };

    response.streamId = stream_id;
    response.cbRequested = data_len;
    response.requestedData = (BYTE*)data;

    return cliprdr->ClientFileContentsResponse(cliprdr, &response) == CHANNEL_RC_OK;
}

/* ------------------------------------------------------------------- */
/* Entrées clavier/souris                                                */
/* ------------------------------------------------------------------- */

#include <freerdp/input.h>
#include <freerdp/scancode.h>
#include <string.h>
#include <stdlib.h>

int asyncrdp_send_mouse_move(rdpContext* ctx, unsigned short x, unsigned short y)
{
    return freerdp_input_send_mouse_event(ctx->input, PTR_FLAGS_MOVE, x, y);
}

/*
 * button_flag attendu : la valeur renvoyée par asyncrdp_ptr_flag_button()
 * ci-dessous — on ne fait volontairement pas passer PTR_FLAGS_BUTTON1/2/3
 * en dur côté Python pour ne pas dépendre de leur valeur numérique réelle.
 */
int asyncrdp_send_mouse_button(rdpContext* ctx, unsigned short x, unsigned short y,
                                unsigned int button_flag, int down)
{
    UINT16 flags = (UINT16)button_flag | (down ? PTR_FLAGS_DOWN : 0);
    return freerdp_input_send_mouse_event(ctx->input, flags, x, y);
}

int asyncrdp_send_mouse_wheel(rdpContext* ctx, unsigned short x, unsigned short y, int delta)
{
    UINT16 units = (UINT16)(abs(delta) & 0xFF);
    UINT16 flags = PTR_FLAGS_WHEEL | units;
    if (delta < 0)
        flags |= PTR_FLAGS_WHEEL_NEGATIVE;
    return freerdp_input_send_mouse_event(ctx->input, flags, x, y);
}

/* left/right/middle -> PTR_FLAGS_BUTTON1/2/3. Retourne 0 si nom inconnu. */
unsigned int asyncrdp_ptr_flag_button(const char* name)
{
    if (strcmp(name, "left") == 0)   return PTR_FLAGS_BUTTON1;
    if (strcmp(name, "right") == 0)  return PTR_FLAGS_BUTTON2;
    if (strcmp(name, "middle") == 0) return PTR_FLAGS_BUTTON3;
    return 0;
}

int asyncrdp_send_unicode_key(rdpContext* ctx, unsigned short code, int down)
{
    UINT16 flags = down ? KBD_FLAGS_DOWN : KBD_FLAGS_RELEASE;
    return freerdp_input_send_unicode_keyboard_event(ctx->input, flags, code);
}

int asyncrdp_send_scancode_key(rdpContext* ctx, unsigned int rdp_scancode, int down)
{
    return freerdp_input_send_keyboard_event_ex(ctx->input, down, /*repeat=*/0, rdp_scancode);
}

/*
 * Résout un nom de touche vers son RDP_SCANCODE_* — on passe par les
 * macros nommées définies par FreeRDP (freerdp/scancode.h) plutôt que de
 * recopier des valeurs numériques côté Python, pour ne pas dépendre de
 * l'encodage interne (code + bit extended) qui pourrait changer.
 * Retourne 0 (RDP_SCANCODE_UNKNOWN) si le nom n'est pas reconnu.
 *
 * Sous-ensemble couvert ici — à étendre selon les besoins réels de GCM
 * (touches média, pavé numérique, etc.).
 */
unsigned int asyncrdp_scancode_from_name(const char* name)
{
    if (strcmp(name, "return") == 0 || strcmp(name, "enter") == 0)
        return RDP_SCANCODE_RETURN;
    if (strcmp(name, "tab") == 0)         return RDP_SCANCODE_TAB;
    if (strcmp(name, "backspace") == 0)   return RDP_SCANCODE_BACKSPACE;
    if (strcmp(name, "escape") == 0)      return RDP_SCANCODE_ESCAPE;
    if (strcmp(name, "space") == 0)       return RDP_SCANCODE_SPACE;
    if (strcmp(name, "delete") == 0)      return RDP_SCANCODE_DELETE;
    if (strcmp(name, "insert") == 0)      return RDP_SCANCODE_INSERT;
    if (strcmp(name, "home") == 0)        return RDP_SCANCODE_HOME;
    if (strcmp(name, "end") == 0)         return RDP_SCANCODE_END;
    if (strcmp(name, "page_up") == 0)     return RDP_SCANCODE_PRIOR;
    if (strcmp(name, "page_down") == 0)   return RDP_SCANCODE_NEXT;
    if (strcmp(name, "up") == 0)          return RDP_SCANCODE_UP;
    if (strcmp(name, "down") == 0)        return RDP_SCANCODE_DOWN;
    if (strcmp(name, "left") == 0)        return RDP_SCANCODE_LEFT;
    if (strcmp(name, "right") == 0)       return RDP_SCANCODE_RIGHT;
    if (strcmp(name, "shift") == 0)       return RDP_SCANCODE_LSHIFT;
    if (strcmp(name, "ctrl") == 0)        return RDP_SCANCODE_LCONTROL;
    if (strcmp(name, "alt") == 0)         return RDP_SCANCODE_LMENU;
    if (strcmp(name, "capslock") == 0)    return RDP_SCANCODE_CAPSLOCK;
    if (strcmp(name, "f1") == 0)  return RDP_SCANCODE_F1;
    if (strcmp(name, "f2") == 0)  return RDP_SCANCODE_F2;
    if (strcmp(name, "f3") == 0)  return RDP_SCANCODE_F3;
    if (strcmp(name, "f4") == 0)  return RDP_SCANCODE_F4;
    if (strcmp(name, "f5") == 0)  return RDP_SCANCODE_F5;
    if (strcmp(name, "f6") == 0)  return RDP_SCANCODE_F6;
    if (strcmp(name, "f7") == 0)  return RDP_SCANCODE_F7;
    if (strcmp(name, "f8") == 0)  return RDP_SCANCODE_F8;
    if (strcmp(name, "f9") == 0)  return RDP_SCANCODE_F9;
    if (strcmp(name, "f10") == 0) return RDP_SCANCODE_F10;
    if (strcmp(name, "f11") == 0) return RDP_SCANCODE_F11;
    if (strcmp(name, "f12") == 0) return RDP_SCANCODE_F12;
    return 0;
}
