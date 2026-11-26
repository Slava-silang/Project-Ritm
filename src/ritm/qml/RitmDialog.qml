pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

Dialog {
    id: dialog
    required property var appearance
    property bool visitorEnabled: false
    padding: appearance.usesSharedStyle ? appearance.dp(20) : 12

    background: RitmControlBackground {
        appearance: dialog.appearance
        kind: "dialog"
        legacyColor: dialog.palette.window
        legacyBorderWidth: 1
        legacyBorder: dialog.palette.dark
    }
    header: Label {
        id: heading
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
        Loader {
            anchors.right: parent.right
            anchors.rightMargin: dialog.appearance.dp(22)
            width: dialog.appearance.dp(58)
            height: width * 0.55
            // On short screens the visitor peeks over the header's inner ledge.
            y: dialog.y >= height ? -height + dialog.appearance.dp(4) : dialog.appearance.dp(2)
            active: dialog.visible && dialog.visitorEnabled
                && (dialog.appearance.observatory || dialog.appearance.jungle)
                && (dialog.y >= height || heading.contentWidth + width + dialog.appearance.dp(54) < heading.width)
            sourceComponent: DialogVisitor {
                objectName: dialog.appearance.jungle ? "jungleVisitor" : "observatoryVisitor"
                reduceMotion: dialog.appearance.reduceMotion
                portrait: dialog.appearance.jungle ? "jungle/monkey.png" : "observatory/visitor.png"
                ledgeColor: dialog.appearance.jungle ? "#9bb675" : "#a89fda"
            }
        }
        Loader {
            parent: dialog.background
            z: -1
            active: dialog.visible && dialog.visitorEnabled && dialog.appearance.jungle
                && dialog.y >= dialog.appearance.dp(24)
            x: dialog.appearance.dp(14)
            width: dialog.appearance.dp(81)
            height: Math.min(dialog.appearance.dp(73), Math.max(0, dialog.y + dialog.appearance.dp(8)))
            y: -height + dialog.appearance.dp(12)
            sourceComponent: Image {
                source: "jungle/fern.png"
                fillMode: Image.PreserveAspectFit
                mipmap: true
            }
        }
        Loader {
            parent: dialog.background
            z: -1
            readonly property real rightGap: dialog.parent ? dialog.parent.width - dialog.x - dialog.width : 0
            active: dialog.visible && dialog.visitorEnabled && dialog.appearance.jungle
                && rightGap >= dialog.appearance.dp(20)
            x: parent ? parent.width - dialog.appearance.dp(9) : 0
            y: dialog.appearance.dp(21)
            width: Math.min(dialog.appearance.dp(48), Math.max(0, rightGap))
            height: dialog.appearance.dp(112)
            sourceComponent: Image {
                source: "jungle/vine.png"
                fillMode: Image.PreserveAspectFit
                mirror: true
                mipmap: true
            }
        }
    }
}
