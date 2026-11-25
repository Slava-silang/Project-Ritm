import QtQuick

QtObject {
    id: appearance
    property string themeKey: "light"
    property real scale: 1
    readonly property bool dusk: themeKey === "dusk"
    readonly property bool graphite: darkPreference
    readonly property bool darkPreference: themeKey === "dark" || dusk
        || (themeKey === "system" && Application.styleHints.colorScheme === Qt.Dark)
    readonly property bool observatory: themeKey === "space"
    property bool reduceMotion: false
    readonly property bool dark: darkPreference || observatory
    readonly property bool usesSharedStyle: dusk || observatory || themeKey === "light" || themeKey === "dark" || themeKey === "system"
    readonly property var colors: dusk ? ({
        background: "#1b2031",
        sidebar: "#151a29",
        surface: "#242b40",
        panel: "#242b40",
        raised: "#303950",
        field: "#1b2133",
        text: "#f0f1fb",
        secondary: "#c5cde4",
        muted: "#b0bad4",
        scrollbar: "#8997bc",
        shadow: "#101525",
        border: "#3b4664",
        controlBorder: "#6e7c9f",
        accent: "#c4ccff",
        accentFill: "#c4ccff",
        accentHover: "#d5dbff",
        accentDown: "#a6b2ed",
        onAccent: "#21294a",
        disabled: "#495473",
        track: "#3b4664",
        selection: "#303c5b",
        work: "#2b3e51",
        event: "#3c3557",
        eventHover: "#51476d",
        eventAccent: "#d2c4ff",
        warningSurface: "#452e42",
        warning: "#ffb8c5"
    }) : observatory ? ({
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
    }) : usesSharedStyle ? ({
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
        background: "#f4f3ed",
        sidebar: "#f4f3ed",
        surface: "#ffffff",
        panel: "#eaece3",
        raised: "#f5f6f0",
        field: "#ffffff",
        text: "#243d33",
        secondary: "#48594e",
        muted: "#69766d",
        border: "#bdcabb",
        controlBorder: "#bdcabb",
        accent: "#2b5949",
        accentFill: "#2b5949",
        accentHover: "#386b57",
        accentDown: "#1d4235",
        onAccent: "#ffffff",
        disabled: "#9eafa2",
        track: "#e0e7dc",
        selection: "#d5e5cc",
        work: "#e6efdf",
        event: "#eee6f5",
        eventHover: "#e0d5eb",
        eventAccent: "#71518d",
        warningSurface: "#fff0e7",
        warning: "#a34425"
    })

    function dp(value) {
        return value * scale;
    }

    function controlRadius(fallback) {
        return dp(dusk ? 14 : usesSharedStyle ? 12 : fallback);
    }

    function dialogRadius(fallback) {
        return dp(dusk ? 24 : usesSharedStyle ? 20 : fallback);
    }
}
