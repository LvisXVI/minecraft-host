package net.lvisxvi.minecrafthost;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.net.wifi.WifiManager;
import android.os.IBinder;
import android.os.PowerManager;

/**
 * Keeps the app process alive while a phone-hosted server runs: a foreground notification with a
 * stop button, a partial wake lock (CPU keeps ticking with the screen off) and a Wi-Fi lock.
 */
public class ServerService extends Service {
    private static final String CHANNEL = "server";
    private static final String ACTION_STOP = "net.lvisxvi.minecrafthost.STOP_ALL";
    private PowerManager.WakeLock wake;
    private WifiManager.WifiLock wifi;

    /** Starts the service when a server runs, stops it when none does. */
    static void update(Context context) {
        Intent intent = new Intent(context, ServerService.class);
        if (LocalHost.get(context).anyRunning()) context.startForegroundService(intent);
        else context.stopService(intent);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            LocalHost.get(this).stopAll();
            return START_NOT_STICKY;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL, "Laufender Server", NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(this, 1, new Intent(this, ServerService.class).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle("Minecraft-Server läuft")
                .setContentText("Auf diesem Handy gehostet · tippen zum Öffnen")
                .setContentIntent(open)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(null, "Server stoppen", stop).build())
                .build();
        startForeground(1, notification);
        if (wake == null) {
            wake = ((PowerManager) getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "mchost:server");
            wake.acquire();
        }
        if (wifi == null) {
            wifi = ((WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE)).createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "mchost:server");
            wifi.acquire();
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        if (wake != null && wake.isHeld()) wake.release();
        if (wifi != null && wifi.isHeld()) wifi.release();
        wake = null;
        wifi = null;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
