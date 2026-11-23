import QtQuick
import QtQuick.Controls

Dialog {
    id: dialog
    required property var appearance
    padding: appearance.usesSharedStyle ? appearance.dp(20) : 12

    background: RitmControlBackground {
        appearance: dialog.appearance
        kind: "dialog"
        legacyColor: dialog.palette.window
        legacyBorderWidth: 1
        legacyBorder: dialog.palette.dark
    }
    header: Label {
        visible: dialog.title.length > 0
        text: dialog.title
        leftPadding: dialog.appearance.usesSharedStyle ? dialog.appearance.dp(20) : 12
        rightPadding: leftPadding
        topPadding: dialog.appearance.usesSharedStyle ? dialog.appearance.dp(16) : 12
        bottomPadding: dialog.appearance.usesSharedStyle ? dialog.appearance.dp(6) : 12
        elide: Text.ElideRight
        font.pixelSize: dialog.appearance.usesSharedStyle ? dialog.appearance.dp(22) : dialog.font.pixelSize
        font.weight: dialog.appearance.usesSharedStyle ? Font.DemiBold : Font.Bold
        color: dialog.appearance.colors.text
    }
}
