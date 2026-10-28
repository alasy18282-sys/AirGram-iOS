(function () {
    let attempts = 0;
    const notifyReady = function () {
        const webApp = window.Telegram && window.Telegram.WebApp;
        if (webApp && typeof webApp.ready === 'function') {
            webApp.ready();
            if (typeof webApp.expand === 'function') webApp.expand();
            return;
        }
        if (++attempts < 100) window.setTimeout(notifyReady, 50);
    };
    notifyReady();
})();
