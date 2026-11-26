import QtQuick

QtObject {
    id: appearance
    property string themeKey: "light"
    property real scale: 1
    readonly property bool darkPreference: themeKey === "dark"
        || (themeKey === "system" && Application.styleHints.colorScheme === Qt.Dark)
    readonly property bool observatory: themeKey === "space"
    readonly property bool jungle: themeKey === "jungle"
    property bool reduceMotion: false
    readonly property bool dark: darkPreference || observatory || jungle
    readonly property bool usesSharedStyle: dark || themeKey === "light" || themeKey === "system"
    readonly property var colors: observatory ? ({
        background: "#090e20",
        sidebar: "#0d1429",
        surface: "#151d36",
        panel: "#121a31",
        raised: "#273452",
        field: "#10172d",
        text: "#f1f3ff",
        secondary: "#d5dbf2",
        muted: "#aab6d5",
        border: "#465477",
        controlBorder: "#7987b5",
        scrollbar: "#8898c9",
        shadow: "#060918",
        accent: "#89e8ef",
        accentFill: "#8ae1ec",
        accentHover: "#aff3fa",
        accentDown: "#65c5d6",
        onAccent: "#101c31",
        disabled: "#65728e",
        track: "#303c60",
        selection: "#223d54",
        work: "#193747",
        event: "#34294b",
        eventHover: "#49375f",
        eventAccent: "#d3b7ff",
        warningSurface: "#412b40",
        warning: "#ffbabb"
    }) : darkPreference ? ({
        background: "#171b1e",
        sidebar: "#121618",
        surface: "#202629",
        panel: "#202629",
        raised: "#2a3336",
        field: "#161b1e",
        text: "#f0f4f3",
        secondary: "#c9d4d3",
        muted: "#abb8b9",
        border: "#343d41",
        controlBorder: "#687679",
        accent: "#9de0c6",
        accentFill: "#9de0c6",
        accentHover: "#b5ead5",
        accentDown: "#81c9ad",
        onAccent: "#10251e",
        disabled: "#3d4949",
        track: "#343d41",
        selection: "#253933",
        work: "#253c36",
        event: "#3c324b",
        eventHover: "#50405f",
        eventAccent: "#d2b6ef",
        warningSurface: "#412d29",
        warning: "#ffb5a7"
    }) : !jungle ? ({
        background: "#f4f7f6",
        shadow: "#4a655c",
        sidebar: "#e8efed",
        surface: "#ffffff",
        panel: "#ffffff",
        raised: "#d5e9e0",
        field: "#fcfefd",
        text: "#182d28",
        secondary: "#364f47",
        muted: "#4b635c",
        border: "#cad8d3",
        controlBorder: "#6d8279",
        scrollbar: "#6d8279",
        accent: "#235c49",
        accentFill: "#235c49",
        accentHover: "#2b7059",
        accentDown: "#194736",
        onAccent: "#ffffff",
        disabled: "#59736a",
        track: "#cad8d3",
        selection: "#dfefe8",
        work: "#dfefe8",
        event: "#eee7f5",
        eventHover: "#e4d8ee",
        eventAccent: "#653d83",
        warningSurface: "#fbe9e4",
        warning: "#923d2e"
    }) : ({
        background: "#101c13",
        sidebar: "#111d14",
        surface: "#1b2b1d",
        panel: "#17271b",
        raised: "#30442c",
        field: "#142218",
        text: "#fff5dc",
        secondary: "#d9dfbd",
        muted: "#bcc9a8",
        border: "#4d6342",
        controlBorder: "#829671",
        scrollbar: "#a0ad80",
        shadow: "#0b140d",
        accent: "#c6e873",
        accentFill: "#bfdf70",
        accentHover: "#d7ed99",
        accentDown: "#a4c559",
        wood: "#57361d",
        woodHover: "#784d27",
        woodDown: "#412918",
        onAccent: "#18220e",
        disabled: "#667054",
        track: "#36482f",
        selection: "#354b21",
        work: "#2a4827",
        event: "#493643",
        eventHover: "#5b4252",
        eventAccent: "#ecc3df",
        warningSurface: "#4a3027",
        warning: "#ffc4a7"
    })

    function dp(value) {
        return value * scale;
    }

    function controlRadius(fallback) {
        return dp(usesSharedStyle ? 12 : fallback);
    }

    function dialogRadius(fallback) {
        return dp(usesSharedStyle ? 20 : fallback);
    }
}
