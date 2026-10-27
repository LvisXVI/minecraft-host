package net.lvisxvi.minecrafthost;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Hosts the bundled UI (assets/www). It either controls Minecraft Host on a PC over HTTP with the
 * pairing code, or runs servers on this phone through {@link LocalHost}.
 */
public class MainActivity extends Activity {
    private WebView web;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(false);
        web.setBackgroundColor(0xFF070B14);
        web.addJavascriptInterface(new Bridge(getSharedPreferences("connection", Context.MODE_PRIVATE), LocalHost.get(this)), "HostApp");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if ("file".equals(url.getScheme())) return false;
                // links such as the Tailscale download open in the browser, never inside the app
                startActivity(new Intent(Intent.ACTION_VIEW, url));
                return true;
            }
        });
        setContentView(web);
        if (savedInstanceState != null) web.restoreState(savedInstanceState);
        else web.loadUrl("file:///android_asset/www/index.html");
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    protected void onPause() {
        super.onPause();
        web.evaluateJavascript("window.hostAppPause && hostAppPause()", null);
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.evaluateJavascript("window.hostAppResume && hostAppResume()", null);
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        // the UI answers "true" when it handled the back step itself (closing a sheet or screen)
        web.evaluateJavascript("window.hostAppBack ? hostAppBack() : false", value -> {
            if (!"true".equals(value)) MainActivity.super.onBackPressed();
        });
    }

    /** Connection storage and the on-phone host for the UI; private to this app. */
    static final class Bridge {
        private final SharedPreferences prefs;
        private final LocalHost local;

        Bridge(SharedPreferences prefs, LocalHost local) {
            this.prefs = prefs;
            this.local = local;
        }

        /** Same API as Minecraft Host on the PC, answered by the server running on this phone. */
        @JavascriptInterface
        public String local(String method, String path, String body) {
            return local.call(method, path, body);
        }

        @JavascriptInterface
        public String get(String key) {
            return prefs.getString(key, "");
        }

        @JavascriptInterface
        public void set(String key, String value) {
            prefs.edit().putString(key, value).apply();
        }
    }
}
