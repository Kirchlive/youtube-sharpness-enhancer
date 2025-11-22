// ==UserScript==
// @name         YouTube Sharpness Enhancer
// @namespace    http://tampermonkey.net/
// @version      1.6.1
// @description  A userscript that adds a sharpness toggle switch for YouTube videos.
// @match        https://www.youtube.com/*
// @grant        GM_setValue
// @grant        GM_getValue
// @license      MIT
// @author       Kirch (Kirchlive)
// @downloadURL  https://github.com/Kirchlive/youtube-sharpness-enhancer/blob/main/youtube-sharpness-enhancer.user.js
// @updateURL    https://github.com/Kirchlive/youtube-sharpness-enhancer/releases
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';

    function addSVGFilter() {
        if (document.getElementById('sharpness-filter')) return;

        const SVG_NS = 'http://www.w3.org/2000/svg';

        const svg = document.createElementNS(SVG_NS, 'svg');
        svg.setAttribute('aria-hidden', 'true');
        svg.style.position = 'absolute';
        svg.style.width = '0';
        svg.style.height = '0';
        svg.style.overflow = 'hidden';

        const defs = document.createElementNS(SVG_NS, 'defs');

        const filter = document.createElementNS(SVG_NS, 'filter');
        filter.setAttribute('id', 'sharpness-filter');

        const blur = document.createElementNS(SVG_NS, 'feGaussianBlur');
        blur.setAttribute('in', 'SourceGraphic');
        blur.setAttribute('stdDeviation', '0.4');
        blur.setAttribute('result', 'blur');

        const conv = document.createElementNS(SVG_NS, 'feConvolveMatrix');
        conv.setAttribute('in', 'SourceGraphic');
        conv.setAttribute('order', '3');
        conv.setAttribute('preserveAlpha', 'true');
        conv.setAttribute('kernelMatrix', '0 -1 0 -1 5 -1 0 -1 0');
        conv.setAttribute('result', 'sharpened');

        const comp = document.createElementNS(SVG_NS, 'feComposite');
        comp.setAttribute('operator', 'in');
        comp.setAttribute('in', 'sharpened');
        comp.setAttribute('in2', 'SourceGraphic');
        comp.setAttribute('result', 'composite');

        const ct = document.createElementNS(SVG_NS, 'feComponentTransfer');
        ct.setAttribute('in', 'composite');

        const r = document.createElementNS(SVG_NS, 'feFuncR');
        r.setAttribute('type', 'linear');
        r.setAttribute('slope', '1.1');
        const g = document.createElementNS(SVG_NS, 'feFuncG');
        g.setAttribute('type', 'linear');
        g.setAttribute('slope', '1.1');
        const b = document.createElementNS(SVG_NS, 'feFuncB');
        b.setAttribute('type', 'linear');
        b.setAttribute('slope', '1.1');

        ct.appendChild(r);
        ct.appendChild(g);
        ct.appendChild(b);

        filter.appendChild(blur);
        filter.appendChild(conv);
        filter.appendChild(comp);
        filter.appendChild(ct);

        defs.appendChild(filter);
        svg.appendChild(defs);
        document.body.appendChild(svg);
    }

    function calculateOptimalContrast(video) {
        try {
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            canvas.width = Math.min(video.videoWidth || 0, 320);
            canvas.height = Math.min(video.videoHeight || 0, 180);
            if (!canvas.width || !canvas.height) throw new Error('no-dim');

            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const data = img.data;

            let luminanceSum = 0;
            for (let i = 0; i < data.length; i += 4) {
                const r = data[i], g = data[i + 1], b = data[i + 2];
                luminanceSum += 0.299 * r + 0.587 * g + 0.114 * b;
            }
            const avg = luminanceSum / (data.length / 4);
            return avg > 128 ? 1.02 : 1.025;
        } catch {
            return 1.02;
        }
    }

    function applySharpnessFilter(video, isEnabled) {
        if (!video) return;
        if (isEnabled) {
            const contrastValue = calculateOptimalContrast(video);
            const brightnessValue = 0.95;
            video.style.filter = `url(#sharpness-filter) contrast(${contrastValue}) brightness(${brightnessValue})`;
        } else {
            video.style.filter = '';
        }
    }

    function addSharpnessToggle() {
        const playerControls = document.querySelector('.ytp-chrome-bottom');
        if (!playerControls) return;

        // change here to inject CSS once
        if (!document.getElementById('sharpness-toggle-style')) {
            const style = document.createElement('style');
            style.id = 'sharpness-toggle-style';
            style.textContent = `
                .switch {
                    position: relative;
                    display: inline-block;
                    width: 45px;
                    height: 25.5px;
                }
                .switch input {
                    opacity: 0;
                    width: 0;
                    height: 0;
                }
                .slider {
                    position: absolute;
                    cursor: pointer;
                    top: 0; left: 0; right: 0; bottom: 0;
                    background-color: #ccc;
                    transition: .4s;
                }
                .slider:before {
                    position: absolute;
                    content: "";
                    height: 19.5px;
                    width: 19.5px;
                    left: 3px;
                    bottom: 3px;
                    background-color: white;
                    transition: .4s;
                }
                input:checked + .slider { background-color: #2196F3; }
                input:checked + .slider:before { transform: translateX(19.5px); }
                .slider.round { border-radius: 25.5px; }
                .slider.round:before { border-radius: 50%; }
            `;
            document.head.appendChild(style);
        }

        let toggleContainer = document.getElementById('sharpness-toggle-container');
        if (!toggleContainer) {
            toggleContainer = document.createElement('div');
            toggleContainer.id = 'sharpness-toggle-container';
            toggleContainer.style.cssText = `
                position: absolute;
                right: 10px;
                top: -40px;
                color: white;
                display: flex;
                align-items: center;
                font-family: Arial, sans-serif;
                z-index: 10;
            `;

            const title = document.createElement('span');
            title.textContent = 'Sharpness Enhancer';
            title.style.marginRight = '10px';
            title.style.fontWeight = 'bold';
            title.style.fontSize = '14px';

            const label = document.createElement('label');
            label.className = 'switch';

            const input = document.createElement('input');
            input.type = 'checkbox';
            input.id = 'sharpness-toggle';

            const slider = document.createElement('span');
            slider.className = 'slider round';

            label.appendChild(input);
            label.appendChild(slider);

            toggleContainer.appendChild(title);
            toggleContainer.appendChild(label);
            playerControls.appendChild(toggleContainer);
        }

        const toggle = document.getElementById('sharpness-toggle');
        const video = document.querySelector('video');

        const savedState = (typeof GM_getValue === 'function')
            ? GM_getValue('sharpnessEnhancerEnabled', false)
            : false;
        if (toggle) toggle.checked = savedState;
        applySharpnessFilter(video, savedState);

        if (toggle && !toggle.__sharpBound) {
            toggle.addEventListener('change', function () {
                if (typeof GM_setValue === 'function') {
                    GM_setValue('sharpnessEnhancerEnabled', this.checked);
                }
                applySharpnessFilter(document.querySelector('video'), this.checked);
            });
            toggle.__sharpBound = true;
        }

        const playerRoot = document.querySelector('#movie_player') || document.body;
        if (playerRoot && !playerRoot.__sharpObserver) {
            const obs = new MutationObserver(() => {
                const v = document.querySelector('video');
                if (v && ((typeof GM_getValue !== 'function') || GM_getValue('sharpnessEnhancerEnabled', false))) {
                    applySharpnessFilter(v, true);
                }
            });
            obs.observe(playerRoot, { subtree: true, childList: true });
            playerRoot.__sharpObserver = obs;
        }
    }

    function waitForVideo() {
        const videoElement = document.querySelector('video');
        if (videoElement) {
            try {
                addSVGFilter();
                addSharpnessToggle();
            } catch (ex) {
                console.log('plugin err: ', ex);
            }
        } else {
            setTimeout(waitForVideo, 1000);
        }
    }

    let lastUrl = location.href;
    new MutationObserver(() => {
        const url = location.href;
        if (url !== lastUrl) {
            lastUrl = url;
            setTimeout(waitForVideo, 1000);
        }
    }).observe(document, { subtree: true, childList: true });

    waitForVideo();
})();
