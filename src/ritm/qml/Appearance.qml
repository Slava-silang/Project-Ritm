import QtQuick

QtObject {
    id: appearance
    property string themeKey: "light"
    property real scale: 1
    readonly property bool darkPreference: themeKey === "dark"
        || (themeKey === "system" && Application.styleHints.colorScheme === Qt.Dark)
    readonly property bool dark: darkPreference || themeKey === "space"
    readonly property bool usesSharedStyle: themeKey === "light" || themeKey === "dark" || themeKey === "system"
    readonly property var colors: darkPreference ? ({
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
    }) : dark ? ({
        background: "#171c20",
        sidebar: "#171c20",
        surface: "#222a30",
        panel: "#1d252b",
        raised: "#2b353d",
        field: "#222a30",
        text: "#ecf1ee",
        secondary: "#b8c7be",
        muted: "#9faea7",
        border: "#485a50",
        controlBorder: "#485a50",
        accent: "#a0d3b4",
        accentFill: "#356c51",
        accentHover: "#427e61",
        accentDown: "#2a5942",
        onAccent: "#ffffff",
        disabled: "#53665a",
        track: "#36473c",
        selection: "#314b3b",
        work: "#293f32",
        event: "#3c324b",
        eventHover: "#50405f",
        eventAccent: "#d2b6ef",
        warningSurface: "#463126",
        warning: "#ffc29b"
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
        return dp(usesSharedStyle ? 12 : fallback);
    }

    function dialogRadius(fallback) {
        return dp(usesSharedStyle ? 20 : fallback);
    }
}
