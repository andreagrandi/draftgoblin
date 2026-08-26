import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Dialog {
    id: root

    required property string applicationVersion
    property var returnFocusItem: null
    readonly property string projectWebsite: "https://github.com/andreagrandi/draftomen"

    objectName: "aboutDialog"
    parent: Overlay.overlay
    modal: true
    focus: true
    title: "About Draft Omen"
    width: Math.min(460, Math.max(300, parent ? parent.width - 32 : 460))
    x: parent ? Math.max(16, Math.round((parent.width - width) / 2)) : 16
    y: parent ? Math.max(16, Math.round((parent.height - height) / 2)) : 16
    padding: 16

    Overlay.modal: Rectangle {
        color: "#99000000"
    }

    background: Rectangle {
        color: Theme.surface
        border.color: Theme.outline
        border.width: 1
        radius: Theme.radius
    }

    header: Rectangle {
        implicitHeight: 52
        color: Theme.surfaceHigh
        border.color: Theme.outline
        border.width: 1

        Label {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            text: root.title
            color: Theme.text
            font.pixelSize: 18
            font.bold: true
            verticalAlignment: Text.AlignVCenter
        }
    }

    onClosed: {
        const opener = root.returnFocusItem
        root.returnFocusItem = null
        if (opener && opener.visible && opener.enabled)
            opener.forceActiveFocus()
    }

    contentItem: ColumnLayout {
        spacing: 12
        implicitWidth: 360

        Label {
            objectName: "aboutDialogTitle"
            Layout.fillWidth: true
            text: "Draft Omen"
            color: Theme.text
            font.pixelSize: 24
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            Accessible.name: text
        }

        Image {
            objectName: "aboutDialogLogo"
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 180
            Layout.preferredHeight: 180
            source: "../assets/draftomen_logo.png"
            sourceSize.width: 360
            sourceSize.height: 360
            fillMode: Image.PreserveAspectFit
            smooth: true
            mipmap: true
            Accessible.name: "Draft Omen logo"
        }

        Label {
            objectName: "aboutDialogVersion"
            Layout.fillWidth: true
            text: "Version " + root.applicationVersion
            color: Theme.text
            horizontalAlignment: Text.AlignHCenter
            Accessible.name: "Draft Omen version " + root.applicationVersion
        }

        Label {
            objectName: "aboutDialogAuthor"
            Layout.fillWidth: true
            text: "Created by Andrea Grandi"
            color: Theme.text
            horizontalAlignment: Text.AlignHCenter
            Accessible.name: text
        }

        DimensionalButton {
            id: websiteButton
            objectName: "aboutDialogWebsite"
            Layout.alignment: Qt.AlignHCenter
            text: "Project website"
            implicitWidth: 140
            Accessible.name: "Open Draft Omen project website"
            Accessible.description: root.projectWebsite
            onClicked: Qt.openUrlExternally(root.projectWebsite)
        }
    }

    footer: DialogButtonBox {
        implicitHeight: 58
        alignment: Qt.AlignRight
        background: Rectangle {
            color: Theme.surfaceHigh
            border.color: Theme.outline
            border.width: 1
        }

        DimensionalButton {
            id: closeButton
            objectName: "aboutDialogCloseButton"
            text: "Close"
            accented: false
            implicitWidth: 96
            Accessible.name: "Close About dialog"
            onClicked: root.close()
        }
    }
}
