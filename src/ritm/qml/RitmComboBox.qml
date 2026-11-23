pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Templates as Templates
import QtQuick.Window

ComboBox {
    id: box
    required property var appearance
    readonly property real hostHeight: Window.height
    palette.dark: appearance.usesSharedStyle ? appearance.colors.muted : appearance.colors.border
    palette.base: appearance.usesSharedStyle ? appearance.colors.field : appearance.colors.surface
    palette.button: appearance.usesSharedStyle ? appearance.colors.field : appearance.colors.raised

    delegate: ItemDelegate {
        id: option
        required property var model
        required property int index
        width: ListView.view.width
        text: model[box.textRole]
        palette.text: box.palette.text
        palette.highlightedText: box.appearance.usesSharedStyle ? box.appearance.colors.accent : box.palette.highlightedText
        font.weight: box.currentIndex === index ? Font.DemiBold : Font.Normal
        highlighted: box.highlightedIndex === index
        hoverEnabled: box.hoverEnabled

        background: Rectangle {
            implicitHeight: 40
            visible: option.down || option.highlighted || option.visualFocus
            radius: box.appearance.usesSharedStyle ? box.appearance.dp(8) : 0
            color: box.appearance.usesSharedStyle ? box.appearance.colors.selection
                : option.down ? box.palette.midlight : box.palette.light
            border.width: option.visualFocus
                || Application.styleHints.accessibility.contrastPreference === Qt.HighContrast ? 1 : 0
            border.color: box.appearance.usesSharedStyle ? box.appearance.colors.accent : box.palette.highlight
        }
    }

    popup: Popup {
        id: choices
        objectName: box.objectName ? box.objectName + "Popup" : ""
        y: box.height + (box.appearance.usesSharedStyle ? box.appearance.dp(4) : 0)
        width: box.width
        height: Math.min(contentItem.implicitHeight + topPadding + bottomPadding,
            Math.max(0, box.hostHeight - topMargin - bottomMargin))
        topMargin: 6
        bottomMargin: 6
        padding: box.appearance.usesSharedStyle ? box.appearance.dp(6) : 1
        font: box.font
        palette: box.palette

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: box.delegateModel
            currentIndex: box.highlightedIndex
            highlightMoveDuration: 0
            spacing: box.appearance.usesSharedStyle ? box.appearance.dp(2) : 0
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle {
            radius: box.appearance.controlRadius(0)
            color: box.appearance.usesSharedStyle ? box.appearance.colors.surface : choices.palette.window
            border.color: box.appearance.usesSharedStyle ? box.appearance.colors.controlBorder : choices.palette.mid
        }
    }

    background: RitmControlBackground {
        appearance: box.appearance
        implicitWidth: appearance.usesSharedStyle ? appearance.dp(120) : 140
        implicitHeight: appearance.usesSharedStyle ? appearance.dp(32) : 40
        kind: "field"
        focused: box.activeFocus
        available: box.enabled
        visible: appearance.usesSharedStyle || !box.flat || box.down
        legacyColor: box.down ? box.palette.mid : box.palette.button
        legacyBorder: !box.editable && box.visualFocus ? box.palette.highlight : box.palette.buttonText
        legacyBorderWidth: !box.editable && box.visualFocus ? 2
            : Application.styleHints.accessibility.contrastPreference === Qt.HighContrast ? 1 : 0
    }
    Binding {
        when: box.appearance.usesSharedStyle && box.editable
        target: (box.contentItem as Templates.TextField)?.background ?? null
        property: "visible"
        value: false
    }
}
