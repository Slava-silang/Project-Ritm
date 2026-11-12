import QtQuick

QtObject {
    id: appearance
    property string themeKey: "light"
    property real scale: 1
    readonly property bool dark: themeKey === "dark" || themeKey === "space"
        || (themeKey === "system" && Application.styleHints.colorScheme === Qt.Dark)
    // Space and jungle currently reuse the base palettes. Their final artwork,
    // typography and component styling can be added here after design approval.
    readonly property var colors: dark ? ({
        background: "#171c20",
        surface: "#222a30",
        panel: "#1d252b",
        raised: "#2b353d",
        text: "#ecf1ee",
        secondary: "#b8c7be",
        muted: "#9faea7",
        border: "#485a50",
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
        surface: "#ffffff",
        panel: "#eaece3",
        raised: "#f5f6f0",
        text: "#243d33",
        secondary: "#48594e",
        muted: "#69766d",
        border: "#bdcabb",
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
}
