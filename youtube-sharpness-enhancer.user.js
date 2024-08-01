// ==UserScript==
// @name         YouTube Sharpness Enhancer
// @namespace    http://tampermonkey.net/
// @version      1.5
// @description  A userscript that adds a sharpness toggle switch for YouTube videos.
// @match        https://www.youtube.com/*
// @grant        GM_setValue
// @grant        GM_getValue
// @license      MIT
// @author       Kirch (Kirchlive)
// @downloadURL  https://github.com/Kirchlive/youtube-sharpness-enhancer/blob/main/youtube-sharpness-enhancer.user.js
// @updateURL    https://github.com/Kirchlive/youtube-sharpness-enhancer/releases
// ==/UserScript==

(function () {
    'use strict';

    function addSVGFilter() {
    const svgFilter = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svgFilter.setAttribute('width', '0');
    svgFilter.setAttribute('height', '0');
    svgFilter.innerHTML = `
        <filter id="sharpness-filter">
            <feGaussianBlur in="SourceGraphic" stdDeviation="0.4" result="blur"/>
            <feConvolveMatrix in="SourceGraphic" order="3" preserveAlpha="true" kernelMatrix="0 -1 0 -1 5 -1 0 -1 0" result="sharpened"/>
            <feComposite operator="in" in="sharpened" in2="SourceGraphic" result="composite"/>
            <feComponentTransfer in="composite">
                <feFuncR type="linear" slope="1.1"/>
                <feFuncG type="linear" slope="1.1"/>
                <feFuncB type="linear" slope="1.1"/>
            </feComponentTransfer>
        </filter>
    `;
    document.body.appendChild(svgFilter);
}
    function calculateOptimalContrast(video) {
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;

        context.drawImage(video, 0, 0, canvas.width, canvas.height);

        const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
        const data = imageData.data;

        let luminanceSum = 0;
        for (let i = 0; i < data.length; i += 4) {
            const r = data[i];
            const g = data[i + 1];
            const b = data[i + 2];
            const luminance = 0.299 * r + 0.587 * g + 0.114 * b;
            luminanceSum += luminance;
        }

        const avgLuminance = luminanceSum / (data.length / 4);
        const contrastValue = avgLuminance > 128 ? 1.02 : 1.025;

        return contrastValue;
    }

    function applySharpnessFilter(video, isEnabled) {
        if (isEnabled) {
            const contrastValue = calculateOptimalContrast(video);
            const brightnessValue = 0.95;
            video.style.filter = `url(#sharpness-filter) contrast(${contrastValue}) brightness(${brightnessValue})`;
        } else {
            video.style.filter = 'none';
        }
    }

    function addSharpnessToggle() {
        const playerControls = document.querySelector('.ytp-chrome-bottom');
        if (!playerControls) return;

        const toggleContainer = document.createElement('div');
        toggleContainer.style.cssText = `
            position: absolute;
            right: 10px;
            top: -40px;
            color: white;
            display: flex;
            align-items: center;
            font-family: Arial, sans-serif;
        `;
        toggleContainer.innerHTML = `
            <span style="margin-right: 10px; font-weight: bold; font-size: 14px;">Sharpness Enhancer</span>
            <label class="switch">
                <input type="checkbox" id="sharpness-toggle">
                <span class="slider round"></span>
            </label>
        `;

        const style = document.createElement('style');
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
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
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
            input:checked + .slider {
                background-color: #2196F3;
            }
            input:checked + .slider:before {
                transform: translateX(19.5px);
            }
            .slider.round {
                border-radius: 25.5px;
            }
            .slider.round:before {
                border-radius: 50%;
            }
        `;

        document.head.appendChild(style);
        playerControls.appendChild(toggleContainer);

        const toggle = toggleContainer.querySelector('#sharpness-toggle');
        const video = document.querySelector('video');

        const savedState = GM_getValue('sharpnessEnhancerEnabled', false);
        toggle.checked = savedState;
        applySharpnessFilter(video, savedState);

        toggle.addEventListener('change', function () {
            GM_setValue('sharpnessEnhancerEnabled', this.checked);
            applySharpnessFilter(video, this.checked);
        });
    }

    function waitForVideo() {
        const videoElement = document.querySelector('video');
        if (videoElement) {
            addSVGFilter();
            addSharpnessToggle();
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
